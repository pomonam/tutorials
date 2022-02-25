# -*- coding: utf-8 -*-
"""
Single-Machine Model Parallel Best Practices
================================
**Author**: `Shen Li <https://mrshenli.github.io/>`_

Model parallel is widely-used in distributed training
techniques. Previous posts have explained how to use
`DataParallel <https://pytorch.org/tutorials/beginner/blitz/data_parallel_tutorial.html>`_
to train a neural network on multiple GPUs; this feature replicates the
same model to all GPUs, where each GPU consumes a different partition of the
input data. Although it can significantly accelerate the training process, it
does not work for some use cases where the model is too large to fit into a
single GPU. This post shows how to solve that problem by using **model parallel**,
which, in contrast to ``DataParallel``, splits a single model onto different GPUs,
rather than replicating the entire model on each GPU (to be concrete, say a model
``m`` contains 10 layers: when using ``DataParallel``, each GPU will have a
replica of each of these 10 layers, whereas when using model parallel on two GPUs,
each GPU could host 5 layers).

The high-level idea of model parallel is to place different sub-networks of a
model onto different devices, and implement the ``forward`` method accordingly
to move intermediate outputs across devices. As only part of a model operates
on any individual device, a set of devices can collectively serve a larger
model. In this post, we will not try to construct huge models and squeeze them
into a limited number of GPUs. Instead, this post focuses on showing the idea
of model parallel. It is up to the readers to apply the ideas to real-world
applications.

.. note::

    For distributed model parallel training where a model spans multiple
    servers, please refer to
    `Getting Started With Distributed RPC Framework <rpc_tutorial.html>`__
    for examples and details.

Basic Usage
-----------
"""

######################################################################
# Let us start with a toy model that contains two linear layers. To run this
# model on two GPUs, simply put each linear layer on a different GPU, and move
# inputs and intermediate outputs to match the layer devices accordingly.
#

















######################################################################
# Note that, the above ``ToyModel`` looks very similar to how one would
# implement it on a single GPU, except the four ``to(device)`` calls which
# place linear layers and tensors on proper devices. That is the only place in
# the model that requires changes. The ``backward()`` and ``torch.optim`` will
# automatically take care of gradients as if the model is on one GPU. You only
# need to make sure that the labels are on the same device as the outputs when
# calling the loss function.












######################################################################
# Apply Model Parallel to Existing Modules
# ----------------------------------------
#
# It is also possible to run an existing single-GPU module on multiple GPUs
# with just a few lines of changes. The code below shows how to decompose
# ``torchvision.models.resnet50()`` to two GPUs. The idea is to inherit from
# the existing ``ResNet`` module, and split the layers to two GPUs during
# construction. Then, override the ``forward`` method to stitch two
# sub-networks by moving the intermediate outputs accordingly.



































######################################################################
# The above implementation solves the problem for cases where the model is too
# large to fit into a single GPU. However, you might have already noticed that
# it will be slower than running it on a single GPU if your model fits. It is
# because, at any point in time, only one of the two GPUs are working, while
# the other one is sitting there doing nothing. The performance further
# deteriorates as the intermediate outputs need to be copied from ``cuda:0`` to
# ``cuda:1`` between ``layer2`` and ``layer3``.
#
# Let us run an experiment to get a more quantitative view of the execution
# time. In this experiment, we train ``ModelParallelResNet50`` and the existing
# ``torchvision.models.resnet50()`` by running random inputs and labels through
# them. After the training, the models will not produce any useful predictions,
# but we can get a reasonable understanding of the execution times.



































######################################################################
# The ``train(model)`` method above uses ``nn.MSELoss`` as the loss function,
# and ``optim.SGD`` as the optimizer. It mimics training on ``128 X 128``
# images which are organized into 3 batches where each batch contains 120
# images. Then, we use ``timeit`` to run the ``train(model)`` method 10 times
# and plot the execution times with standard deviations.










































######################################################################
#
# .. figure:: /_static/img/model-parallel-images/mp_vs_rn.png
#    :alt:
#
# The result shows that the execution time of model parallel implementation is
# ``4.02/3.75-1=7%`` longer than the existing single-GPU implementation. So we
# can conclude there is roughly 7% overhead in copying tensors back and forth
# across the GPUs. There are rooms for improvements, as we know one of the two
# GPUs is sitting idle throughout the execution. One option is to further
# divide each batch into a pipeline of splits, such that when one split reaches
# the second sub-network, the following split can be fed into the first
# sub-network. In this way, two consecutive splits can run concurrently on two
# GPUs.

######################################################################
# Speed Up by Pipelining Inputs
# -----------------------------
#
# In the following experiments, we further divide each 120-image batch into
# 20-image splits. As PyTorch launches CUDA operations asynchronously, the
# implementation does not need to spawn multiple threads to achieve
# concurrency.





































######################################################################
# Please note, device-to-device tensor copy operations are synchronized on
# current streams on the source and the destination devices. If you create
# multiple streams, you have to make sure that copy operations are properly
# synchronized. Writing the source tensor or reading/writing the destination
# tensor before finishing the copy operation can lead to undefined behavior.
# The above implementation only uses default streams on both source and
# destination devices, hence it is not necessary to enforce additional
# synchronizations.
#
# .. figure:: /_static/img/model-parallel-images/mp_vs_rn_vs_pp.png
#    :alt:
#
# The experiment result shows that, pipelining inputs to model parallel
# ResNet50 speeds up the training process by roughly ``3.75/2.51-1=49%``. It is
# still quite far away from the ideal 100% speedup. As we have introduced a new
# parameter ``split_sizes`` in our pipeline parallel implementation, it is
# unclear how the new parameter affects the overall training time. Intuitively
# speaking, using small ``split_size`` leads to many tiny CUDA kernel launch,
# while using large ``split_size`` results to relatively long idle times during
# the first and last splits. Neither are optimal. There might be an optimal
# ``split_size`` configuration for this specific experiment. Let us try to find
# it by running experiments using several different ``split_size`` values.
























######################################################################
#
# .. figure:: /_static/img/model-parallel-images/split_size_tradeoff.png
#    :alt:
#
# The result shows that setting ``split_size`` to 12 achieves the fastest
# training speed, which leads to ``3.75/2.43-1=54%`` speedup. There are
# still opportunities to further accelerate the training process. For example,
# all operations on ``cuda:0`` is placed on its default stream. It means that
# computations on the next split cannot overlap with the copy operation of the
# prev split. However, as prev and next splits are different tensors, there is
# no problem to overlap one's computation with the other one's copy. The
# implementation need to use multiple streams on both GPUs, and different
# sub-network structures require different stream management strategies. As no
# general multi-stream solution works for all model parallel use cases, we will
# not discuss it in this tutorial.
#
# **Note:**
#
# This post shows several performance measurements. You might see different
# numbers when running the same code on your own machine, because the result
# depends on the underlying hardware and software. To get the best performance
# for your environment, a proper approach is to first generate the curve to
# figure out the best split size, and then use that split size to pipeline
# inputs.
#

# %%%%%%RUNNABLE_CODE_REMOVED%%%%%%
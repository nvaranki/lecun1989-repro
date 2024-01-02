"""
Running this script eventually gives:
23
eval: split train. loss 4.073383e-03. error 0.62%. misses: 45
eval: split test . loss 2.838382e-02. error 4.09%. misses: 82
local run
eval: split train. loss 5.234058e-03. error 0.86%. misses: 63
eval: split test . loss 2.743839e-02. error 3.79%. misses: 76
23 CUDA GeForce 1070Ti (too low batch size to be efficient)
eval: split train. loss 6.008524e-03. error 0.81%. misses: 59
eval: split test . loss 2.932071e-02. error 4.48%. misses: 90
padding in conv2d:
eval: split train. loss 7.048257e-03. error 0.91%. misses: 65
eval: split test . loss 3.134866e-02. error 4.68%. misses: 94
slice3 version 2
eval: split train. loss 5.174854e-03. error 0.74%. misses: 54
eval: split test . loss 2.841093e-02. error 4.38%. misses: 87
kernel of 3x3 size
eval: split train. loss 9.977578e-03. error 1.37%. misses: 100
eval: split test . loss 3.101756e-02. error 4.24%. misses: 84
kernel of 3x3 size, 46 steps
eval: split train. loss 4.674537e-03. error 0.78%. misses: 57
eval: split test . loss 3.018412e-02. error 4.48%. misses: 89
"""

import os
import json
import argparse

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from tensorboardX import SummaryWriter # pip install tensorboardX

# -----------------------------------------------------------------------------


class Net(nn.Module):
    """ 1989 LeCun ConvNet per description in the paper """

    def __init__(self, s1: int, n1, n2, s3, n3, n4, np=2):
        super().__init__()
        self.np = np

        # initialization as described in the paper to my best ability, but it doesn't look right...
        winit = lambda fan_in, *shape: (torch.rand(*shape) - 0.5) * 2 * 2.4 / fan_in**0.5
        macs = 0 # keep track of MACs (multiply accumulates)
        acts = 0 # keep track of number of activations

        # H1 layer parameters and their initialization
        self.H1w = nn.Parameter(winit(s1 * s1 * 1, n1, 1, s1, s1))  # 12*1*5*5 kernels
        self.H1b = nn.Parameter(torch.zeros(n1, n2, n2)) # presumably init to zero for biases
        macs += (s1 * s1 * 1) * (n2 * n2) * n1
        acts += (n2 * n2) * n1

        # H2 layer parameters and their initialization
        """
        H2 neurons all connect to only 8 of the 12 input planes, with an unspecified pattern
        I am going to assume the most sensible block pattern where 4 planes at a time connect
        to differently overlapping groups of 8/12 input planes. We will implement this with 3
        separate convolutions that we concatenate the results of.
        """
        self.H2w = nn.Parameter(winit(s1 * s1 * n2, n1, n2, s1, s1))  # 12*8*5*5 kernels
        self.H2b = nn.Parameter(torch.zeros(n1, s3, s3)) # presumably init to zero for biases
        macs += (s1 * s1 * n2) * (s3 * s3) * n1
        acts += (s3 * s3) * n1

        # H3 is a fully connected layer
        self.H3w = nn.Parameter(winit(s3 * s3 * n1, s3 * s3 * n1, n3))  # 192*30
        self.H3b = nn.Parameter(torch.zeros(n3))
        macs += (s3 * s3 * n1) * n3
        acts += n3

        # output layer is also fully connected layer
        self.outw = nn.Parameter(winit(n3, n3, n4))  # 30*10
        self.outb = nn.Parameter(-torch.ones(n4)) # 9/10 targets are -1, so makes sense to init slightly towards it
        macs += n3 * n4
        acts += n4

        self.macs = macs
        self.acts = acts

    def forward(self, x):
        p = self.np

        # x has shape (1, 1, 16, 16)
        x = F.pad(x, (p, p, p, p), 'constant', -1.0) # pad by two using constant -1 for background
        # NV x = F.conv2d(x, self.H1w, stride=2, padding=0 if p == 1 else p) + self.H1b
        x = F.conv2d(x, self.H1w, stride=2) + self.H1b
        x = torch.tanh(x)

        # x is now shape (1, 12, 8, 8)
        x = F.pad(x, (p, p, p, p), 'constant', -1.0) # pad by two using constant -1 for background
        slice1 = F.conv2d(x[:, 0:8], self.H2w[0:4], stride=2) # first 4 planes look at first 8 input planes
        slice2 = F.conv2d(x[:, 4:12], self.H2w[4:8], stride=2) # next 4 planes look at last 8 input planes
        slice3 = F.conv2d(x[:, 2:10], self.H2w[8:12], stride=2) # last 4 planes are ... (version 2)
        x = torch.cat((slice1, slice2, slice3), dim=1) + self.H2b
        x = torch.tanh(x)

        # x is now shape (1, 12, 4, 4)
        x = x.flatten(start_dim=1) # (1, 12*4*4)
        x = x @ self.H3w + self.H3b
        x = torch.tanh(x)

        # x is now shape (1, 30)
        x = x @ self.outw + self.outb
        x = torch.tanh(x)

        # x is finally shape (1, 10)
        return x

# -----------------------------------------------------------------------------


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description="Train a 1989 LeCun ConvNet on digits")
    parser.add_argument('--learning-rate', '-l', type=float, default=0.03, help="SGD learning rate") # 0.3 - no convergence; 0.003 - too slow and not perfect
    parser.add_argument('--output-dir'   , '-o', type=str,   default='out/base', help="output directory for training logs")
    args = parser.parse_args()
    print(vars(args))

    # init rng
    torch.manual_seed(1337)
    np.random.seed(1337)
    torch.use_deterministic_algorithms(True)

    # set up logging
    os.makedirs(args.output_dir, exist_ok=True)
    with open(os.path.join(args.output_dir, 'args.json'), 'w') as f:
        json.dump(vars(args), f, indent=2)
    writer = SummaryWriter(args.output_dir)

    # init a model
    device = torch.device("cuda:0")
    # device = torch.device("cpu")
    model = Net(5, 12, 8, 4, 30, 10, 2).to(device)
    # NV model = Net(3, 12, 8, 4, 30, 10, 1).to(device)
    print("model stats:")
    print("# kernels:     ", (5, 12, 8, 4, 30, 10))
    # NV print("# kernels:     ", (3, 12, 8, 4, 30, 10, 1))
    print("# params:      ", sum(p.numel() for p in model.parameters())) # in paper total is 9,760
    print("# MACs:        ", model.macs)
    print("# activations: ", model.acts)

    # init data
    Xtr, Ytr = torch.load('train1989.pt', device)
    Xte, Yte = torch.load('test1989.pt', device)

    # init optimizer
    optimizer = optim.SGD(model.parameters(), lr=args.learning_rate)

    def eval_split(split):
        # eval the full train/test set, batched implementation for efficiency
        model.eval()
        X, Y = (Xtr, Ytr) if split == 'train' else (Xte, Yte)
        Yhat = model(X)
        loss = torch.mean((Y - Yhat)**2)
        err = torch.mean((Y.argmax(dim=1) != Yhat.argmax(dim=1)).float())
        print(f"eval: split {split:5s}. loss {loss.item():e}. error {err.item()*100:.2f}%. misses: {int(err.item()*Y.size(0))}")
        writer.add_scalar(f'error/{split}', err.item()*100, pass_num)
        writer.add_scalar(f'loss/{split}', loss.item(), pass_num)

    # train
    for pass_num in range(23):

        # perform one epoch of training
        model.train()
        for step_num in range(Xtr.size(0)):

            # fetch a single example into a batch of 1
            x, y = Xtr[[step_num]], Ytr[[step_num]]

            # forward the model
            yhat = model(x) # runs model.forward(x)

            # calculate the loss and the gradient and update the parameters
            optimizer.zero_grad(set_to_none=True)
            torch.mean((y - yhat)**2).backward() #TODO how does it link to model/optimizer?
            optimizer.step()

        # after epoch evaluate the train and test error / metrics
        print(pass_num + 1)
        eval_split('train')
        eval_split('test')

    # save final model to file
    torch.save(model.state_dict(), os.path.join(args.output_dir, 'model.pt'))

    # print("model.H1")
    # print(model.H1w)
    # print(model.H1b)
    #
    # print("model.H2")
    # print(model.H2w)
    # print(model.H2b)

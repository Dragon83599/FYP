"""Lesson 06: print one complete PyTorch training step.

Run from the repository root:
    E:\\UserData\\Documents\\ChatGPT\\FYP\\.venv\\Scripts\\python.exe learning\\lesson06_torch_one_step.py

This script uses three small examples so every number is easy to check by hand.
"""

import torch
from torch import nn

# Fix the random seed so the initial weight and bias are reproducible.
torch.manual_seed(0)

# Three training examples: x = -1, 0, 1.
# The target is y = 2x + 1, so y = -1, 1, 3.
x = torch.tensor([[-1.0], [0.0], [1.0]])
y = 2 * x + 1

# A linear model has one weight and one bias: y_pred = weight * x + bias.
model = nn.Linear(1, 1)
loss_fn = nn.MSELoss()
optimizer = torch.optim.SGD(model.parameters(), lr=0.1)

print("=== before training ===")
print("weight =", model.weight.item())
print("bias =", model.bias.item())

# Forward pass: compute predictions with the random initial parameters.
prediction = model(x)
print("prediction =", prediction.detach().numpy().ravel())
print("target =", y.numpy().ravel())

# Compute the mean squared error.
loss = loss_fn(prediction, y)
print("loss =", loss.item())

# Clear old gradients, compute new gradients, and print them.
optimizer.zero_grad()
loss.backward()
print("weight.grad =", model.weight.grad.item())
print("bias.grad =", model.bias.grad.item())

# Update the parameters once.
optimizer.step()
print("=== after one update ===")
print("weight =", model.weight.item())
print("bias =", model.bias.item())

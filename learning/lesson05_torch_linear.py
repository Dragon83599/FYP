import torch
from torch import nn

torch.manual_seed(0)

x = torch.linspace(-1, 1, 100).unsqueeze(1)
y = 2 * x + 1

model = nn.Linear(1, 1)
loss_fn = nn.MSELoss()
optimizer = torch.optim.SGD(model.parameters(), lr=0.01)

for epoch in range(200):
    prediction = model(x)
    loss = loss_fn(prediction, y)

    optimizer.zero_grad()
    loss.backward()
    optimizer.step()

    if epoch % 20 == 0:
        print(
            epoch,
            round(loss.item(), 6),
            round(model.weight.item(), 6),
            round(model.bias.item(), 6),
        )

print("final weight =", round(model.weight.item(), 6))
print("final bias =", round(model.bias.item(), 6))
"""Lesson 07: Dataset and DataLoader with a tiny binary classifier.

Run from the repository root:
    .venv\\Scripts\\python.exe learning\\lesson07_dataset_dataloader.py
"""

import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

torch.manual_seed(0)

# Class 0 is around (-1, -1). Class 1 is around (1, 1).
features = torch.tensor(
    [
        [-1.0, -1.0],
        [-0.8, -1.1],
        [-1.2, -0.9],
        [-0.9, -0.7],
        [-1.1, -1.2],
        [-0.7, -1.3],
        [-1.3, -0.8],
        [-1.0, -0.6],
        [-0.6, -1.0],
        [-1.2, -1.0],
        [1.0, 1.0],
        [0.8, 1.1],
        [1.2, 0.9],
        [0.9, 0.7],
        [1.1, 1.2],
        [0.7, 1.3],
        [1.3, 0.8],
        [1.0, 0.6],
        [0.6, 1.0],
        [1.2, 1.0],
    ]
)
labels = torch.tensor(
    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
     1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
)


class SimpleDataset(Dataset):
    """Map each index to one (feature, label) pair."""

    def __init__(self, features: torch.Tensor, labels: torch.Tensor) -> None:
        self.features = features
        self.labels = labels

    def __len__(self) -> int:
        return len(self.labels)

    def __getitem__(self, index: int):
        return self.features[index], self.labels[index]


dataset = SimpleDataset(features, labels)
loader = DataLoader(dataset, batch_size=4, shuffle=True)

model = nn.Sequential(
    nn.Linear(2, 2),
    nn.ReLU(),
    nn.Linear(2, 1),
)
loss_fn = nn.BCEWithLogitsLoss()
optimizer = torch.optim.Adam(model.parameters(), lr=0.01)

for epoch in range(1, 51):
    model.train()
    running_loss = 0.0

    for batch_features, batch_labels in loader:
        logits = model(batch_features).squeeze(1)
        loss = loss_fn(logits, batch_labels)

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        running_loss += loss.item() * len(batch_features)

    if epoch % 10 == 0:
        model.eval()
        with torch.no_grad():
            logits = model(features).squeeze(1)
            probabilities = torch.sigmoid(logits)
            predictions = (probabilities >= 0.5).float()
            accuracy = (predictions == labels).float().mean()

        epoch_loss = running_loss / len(dataset)
        print(epoch, round(epoch_loss, 4), round(accuracy.item(), 4))

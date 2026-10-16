"""Lesson 10: save and load model state, and record reproducibility data.

Run from the repository root:
    .venv\\Scripts\\python.exe learning\\lesson10_save_load.py
"""

import json
from pathlib import Path

import torch
from torch import nn

torch.manual_seed(0)

x = torch.linspace(-1.0, 1.0, 100).unsqueeze(1)
y = 2.0 * x + 1.0


def make_model():
    """Create the same architecture every time."""
    return nn.Linear(1, 1)


model = make_model()
loss_fn = nn.MSELoss()
optimizer = torch.optim.SGD(model.parameters(), lr=0.1)

for epoch in range(200):
    prediction = model(x)
    loss = loss_fn(prediction, y)

    optimizer.zero_grad()
    loss.backward()
    optimizer.step()

output_dir = Path("learning/outputs/lesson10")
output_dir.mkdir(parents=True, exist_ok=True)

model_path = output_dir / "linear_state.pt"
config_path = output_dir / "config.json"
metrics_path = output_dir / "metrics.json"

# Save only the learned tensors. The code must recreate the model architecture.
torch.save(model.state_dict(), model_path)

config = {
    "model": "nn.Linear(1, 1)",
    "input_features": 1,
    "output_features": 1,
    "learning_rate": 0.1,
    "epochs": 200,
    "seed": 0,
    "data": "x in [-1, 1], y = 2x + 1",
}
config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

model.eval()
with torch.no_grad():
    final_prediction = model(x)
    final_loss = loss_fn(final_prediction, y).item()

metrics_path.write_text(
    json.dumps({"final_loss": final_loss}, indent=2),
    encoding="utf-8",
)

# Recreate the architecture first, then load the saved tensors.
loaded_model = make_model()
loaded_state = torch.load(model_path, map_location="cpu", weights_only=True)
loaded_model.load_state_dict(loaded_state)
loaded_model.eval()

with torch.no_grad():
    original_output = model(x)
    loaded_output = loaded_model(x)
    max_difference = (original_output - loaded_output).abs().max().item()

print("saved model        =", model_path)
print("saved config       =", config_path)
print("saved metrics      =", metrics_path)
print("final loss         =", round(final_loss, 8))
print("weight             =", round(model.weight.item(), 6))
print("bias               =", round(model.bias.item(), 6))
print("max difference     =", max_difference)
print("first 3 predictions =", original_output[:3].flatten().tolist())

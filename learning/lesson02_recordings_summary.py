import csv
from collections import Counter
from pathlib import Path

csv_path = Path("data/processed/circor-subset/recordings.csv")

with csv_path.open(newline="", encoding="utf-8") as handle:
    rows = list(csv.DictReader(handle))

train_subjects = {
    row["subject_id"]
    for row in rows
    if row["split"] == "train"
}
print("train subjects =", len(train_subjects))

print("total recordings =", len(rows))
print("split counts =", Counter(row["split"] for row in rows))
print("label counts =", Counter(row["label"] for row in rows))
print(
    "split x label =",
    Counter((row["split"], row["label"]) for row in rows),
)

location_counts = Counter(row["location"] for row in rows)
print("location counts =", location_counts)
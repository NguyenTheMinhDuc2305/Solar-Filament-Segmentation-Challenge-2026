"""One fold of training: dataset, augmentation, loss, and the training loop.

Reads the resized image/mask PNG cache `data.cache_train_arrays` builds, so no
fold re-decodes the original 2048x2048 JPEGs or re-rasterizes polygons.
"""
from __future__ import annotations

from pathlib import Path

import albumentations as A
import cv2
import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

from src.model import build_model

IMAGENET_MEAN = 0.449  # single-channel approximation of the 3-channel ImageNet mean
IMAGENET_STD = 0.226


class FilamentDataset(Dataset):
    """Cached grayscale image + binary union mask, resized to `img_size`."""

    def __init__(self, img_dir, mask_dir, stems, augment):
        self.img_dir = Path(img_dir)
        self.mask_dir = Path(mask_dir)
        self.stems = list(stems)
        self.augment = augment

    def __len__(self):
        return len(self.stems)

    def __getitem__(self, idx):
        stem = self.stems[idx]
        img = cv2.imread(str(self.img_dir / (stem + ".png")), cv2.IMREAD_GRAYSCALE)
        mask = cv2.imread(str(self.mask_dir / (stem + ".png")), cv2.IMREAD_GRAYSCALE)
        if img is None or mask is None:
            raise FileNotFoundError("missing cached array for stem: {}".format(stem))
        mask = (mask > 127).astype(np.float32)

        if self.augment is not None:
            out = self.augment(image=img, mask=mask)
            img, mask = out["image"], out["mask"]

        img = img.astype(np.float32) / 255.0
        img = (img - IMAGENET_MEAN) / IMAGENET_STD
        img3 = np.repeat(img[None, :, :], 3, axis=0).astype(np.float32)
        mask = mask.astype(np.float32)[None, :, :]
        return torch.from_numpy(img3), torch.from_numpy(mask), stem


def build_augmentation():
    """Flips, full rotation, brightness/contrast - deliberately no CLAHE."""
    return A.Compose([
        A.HorizontalFlip(p=0.5),
        A.VerticalFlip(p=0.5),
        A.Rotate(limit=180, border_mode=cv2.BORDER_CONSTANT, value=0, mask_value=0, p=0.7),
        A.RandomBrightnessContrast(brightness_limit=0.2, contrast_limit=0.2, p=0.5),
    ])


def soft_dice_loss(logits, target, eps=1e-6):
    prob = torch.sigmoid(logits)
    dims = (1, 2, 3)
    intersection = (prob * target).sum(dims)
    union = prob.sum(dims) + target.sum(dims)
    dice = (2 * intersection + eps) / (union + eps)
    return 1.0 - dice.mean()


def combined_loss(logits, target):
    """0.5 BCE + 0.5 soft Dice, as specified in the proposal."""
    bce = torch.nn.functional.binary_cross_entropy_with_logits(logits, target)
    dice = soft_dice_loss(logits, target)
    return 0.5 * bce + 0.5 * dice


def train_fold(img_dir, mask_dir, train_stems, cfg, device, log=print):
    """Train one fold's model on `train_stems`; returns the trained model.

    AMP is only enabled on CUDA - it is a no-op (and occasionally unsupported)
    on CPU, which is the only device this repo's smoke test ever runs on.
    """
    model = build_model(cfg["encoder"], cfg.get("encoder_weights", "imagenet")).to(device)
    dataset = FilamentDataset(img_dir, mask_dir, train_stems, augment=build_augmentation())
    loader = DataLoader(
        dataset,
        batch_size=cfg["batch_size"],
        shuffle=True,
        num_workers=cfg.get("num_workers", 2),
        drop_last=len(dataset) > cfg["batch_size"],
    )

    epochs = cfg["epochs"]
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg["lr"])
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=max(epochs, 1), eta_min=cfg.get("lr_min", 1e-6))
    use_amp = device.type == "cuda"
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp)

    model.train()
    for epoch in range(epochs):
        running_loss = 0.0
        n_batches = 0
        for images, masks, _stems in loader:
            images = images.to(device, non_blocking=True)
            masks = masks.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", enabled=use_amp):
                logits = model(images)
                loss = combined_loss(logits, masks)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            running_loss += loss.item()
            n_batches += 1
        scheduler.step()
        log("  epoch {}/{} loss={:.4f}".format(
            epoch + 1, epochs, running_loss / max(n_batches, 1)))

    model.eval()
    return model


@torch.no_grad()
def predict_prob(model, img_dir, stem, img_size, device):
    """One cached image -> sigmoid probability map at `img_size`x`img_size`."""
    img = cv2.imread(str(Path(img_dir) / (stem + ".png")), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError("missing cached image for stem: {}".format(stem))
    if img.shape[0] != img_size or img.shape[1] != img_size:
        img = cv2.resize(img, (img_size, img_size), interpolation=cv2.INTER_AREA)
    img = img.astype(np.float32) / 255.0
    img = (img - IMAGENET_MEAN) / IMAGENET_STD
    img3 = np.repeat(img[None, None, :, :], 3, axis=1).astype(np.float32)
    tensor = torch.from_numpy(img3).to(device)
    logits = model(tensor)
    prob = torch.sigmoid(logits)[0, 0].detach().cpu().numpy()
    return prob

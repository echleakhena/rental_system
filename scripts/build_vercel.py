"""Publish only application static assets to Vercel's public directory."""

from pathlib import Path
from shutil import copytree


def main():
    root = Path(__file__).resolve().parents[1]
    copytree(root / "rpms/static", root / "public/static", dirs_exist_ok=True)
    print("Prepared public/static for Vercel.")


if __name__ == "__main__":
    main()

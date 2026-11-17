"""Format CSS and JavaScript consistently, or verify formatting with --check."""

import argparse
from pathlib import Path

import cssbeautifier
import jsbeautifier


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Check without writing files"
    )
    arguments = parser.parse_args()
    static = Path(__file__).resolve().parents[1] / "rpms" / "static"
    changed = []

    for pattern, formatter in [("*.css", cssbeautifier), ("*.js", jsbeautifier)]:
        options = formatter.default_options()
        options.indent_size = 2
        for path in sorted(static.glob(pattern)):
            original = path.read_text()
            formatted = formatter.beautify(original, options).rstrip() + "\n"
            if formatted == original:
                continue
            changed.append(path.name)
            if not arguments.check:
                path.write_text(formatted)

    if arguments.check and changed:
        print("Formatting needed: " + ", ".join(changed))
        return 1
    print(
        "Frontend formatting is consistent."
        if not changed
        else "Formatted: " + ", ".join(changed)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

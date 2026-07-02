import sys

from dependency_check import dependency_message
from dependency_check import missing_dependencies


def main() -> None:
    """Command-line entry point."""
    missing = missing_dependencies()

    if missing:
        sys.stderr.write(dependency_message(missing) + "\n")
        return

    from services.weekly_report import generate_weekly

    generate_weekly()


if __name__ == "__main__":
    main()

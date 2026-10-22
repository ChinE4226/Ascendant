"""Start the development supervisor before importing application code."""

from pathlib import Path

from ascendant.web.reload import parse_args, run


def main() -> None:
    args = parse_args()
    if not args.no_reload:
        run(args, Path(__file__).resolve().parents[2])
        return

    from ascendant.web.server import main as serve

    serve(args)


if __name__ == "__main__":
    main()

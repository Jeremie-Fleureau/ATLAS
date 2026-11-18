# atlas/__main__.py
from pathlib import Path
from atlas.cli.app import AtlasApp


def main() -> None:
    config_path = Path(__file__).resolve().parent.parent / "config" / "profiles.yaml"
    app = AtlasApp(config_path=config_path)
    app.run()


if __name__ == "__main__":
    main()

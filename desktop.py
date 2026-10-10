"""PyInstaller entry point; configuration files are never bundled."""
from dangerbot.gui import main


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from dangerbot.packaging_check import self_test
        self_test(sys.argv[2])
    else:
        main()

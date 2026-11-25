"""`python -m rogertabs`, which is what the `rogertabs` command runs.

`update` is dispatched before anything else is imported (see entry.py). On
Windows a loaded module's files (numpy's DLLs, an .exe launcher) cannot be
replaced while this process runs, and the updater's job is to replace them.
"""

if __name__ == "__main__":
    from rogertabs.entry import main

    main()

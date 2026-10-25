"""Run Wander:  python main.py   (see --help for options)"""

import os

os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')

from wander.game import main  # noqa: E402

if __name__ == '__main__':
    main()

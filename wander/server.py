"""Run a dedicated Wander arena server:  python server.py [--port 8765]

Optional: set GEMINI_API_KEY first so bots get Gemini strategy and taunts."""

from wander.server import main

if __name__ == '__main__':
    main()

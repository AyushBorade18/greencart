"""
Entry point.
  * Vercel imports `app` from this file.
  * On your own computer:  python app.py   ->  http://127.0.0.1:5000
"""
from shop import create_app, config

app = create_app()

if __name__ == "__main__":
    app.run(debug=config.DEBUG, port=5000)

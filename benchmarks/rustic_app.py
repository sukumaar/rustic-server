import sys

from rustic_server import App

app = App()
app.get("/health", json={"status": "ok"})

if __name__ == "__main__":
    app.run(port=int(sys.argv[1]))

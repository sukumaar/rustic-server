from rustic_server import App

app = App()
app.get("/health", json={"status": "ok"})
app.get("/", json={"message": "Python declarations. Rust execution."})
app.route("POST", "/accepted", json={"accepted": True}, status=202)
if __name__ == "__main__":
    app.run()

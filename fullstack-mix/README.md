# fullstack-mix/

Mixed projects that combine a front end and a back end. Each project is split into `client/` and `server/`.

## Projects

- [`link-vault/`](link-vault/) — a self-hosted bookmark manager: a stdlib Python
  server (SQLite + REST API) with a vanilla HTML/CSS/JS client.
- [`flashcards/`](flashcards/) — a flashcard app with a spaced-repetition
  scheduler: a stdlib Python server with a vanilla HTML/CSS/JS client.
- [`webhook-inbox/`](webhook-inbox/) — capture, inspect and replay webhook
  deliveries locally: a stdlib Python server (SQLite + REST API) with a vanilla
  HTML/CSS/JS inspector.

## Conventions

```
fullstack-mix/<project-name>/
├── README.md     # what the project does and how to run both halves
├── client/       # front-end code
└── server/       # back-end code
```

Each project's README explains how to start the server and the client.

# Closeout (demo)

Closeout turns finished home-repair jobs into verified records. Each job can carry up to three marks: the homeowner confirmed it, the contractor confirmed it, and a town permit is on file.

This is a demo that runs on your own computer. **Every person, home and company in it is made up** and not tied to any real company. Emails go only to a local test mailbox. Real public data (Manasquan, NJ permits and parcels) appears only on the data pages, with no people.

> Status: being built slice by slice. See [the plan](docs/06-IMPLEMENTATION-PLAN.md).

## Quickstart

You need Docker and `make` (plus [uv](https://docs.astral.sh/uv/) for the host-side tools).

```sh
make up      # start everything (http://127.0.0.1:8000)
make down    # stop, keeping all data
make reset   # wipe everything and load the seed again
```

## How much memory it needs

The Docker services use about 310 MB together when idle (measured on a Mac: app 79 MB, Temporal 84 MB, worker 71 MB, sender 30 MB, Postgres 27 MB, Mailpit 18 MB). Replay mode needs nothing more. Light mode adds about 4.5 GB for one local model, and full mode about 11 GB for two.

## Local models on Linux

On a Mac, Docker reaches Ollama at `host.docker.internal`. On Linux, compose maps that name to the host (`host-gateway`), but Ollama listens only on 127.0.0.1 by default, which containers can't reach. Start it on the Docker bridge address instead, which is still not reachable from other machines:

```sh
OLLAMA_HOST=172.17.0.1:11434 ollama serve
```

Don't use `0.0.0.0`: that opens Ollama to your whole network. (This Linux setup is written down but has not been tried yet. Replay mode needs no models at all.)

## Runs on your computer only

Every service listens on 127.0.0.1, so nothing is reachable from other machines. Emails go only to the local Mailpit mailbox, and only to made-up `.example` and `.test` addresses.

The one exception is **hosted mode**, which you turn on yourself. It sends invoice images to an outside AI company, so it is for made-up documents only. A privacy request can't reach copies held by that company.

## Credits

- Construction permits: NJ Department of Community Affairs, via data.nj.gov (public domain).
- Parcels: NJ Office of GIS (NJOGIS).

## License

MIT. See [LICENSE](LICENSE).

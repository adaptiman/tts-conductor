# User Journey

![User Journey Workflow](docs/user-journey.png)

## Mermaid Source

```mermaid
flowchart TD
  A[User enters Daily room] --> B[Bot/app launch trigger]
  B --> C[Initialize app session]
  C --> D[Connect/auth to Instapaper]
  D --> E[Navigation mode enabled]

  E --> F{User command?}
  F -->|next| G[Move to next article]
  F -->|previous| H[Move to previous article]
  F -->|delete| I[Delete article]
  F -->|archive| J[Archive article]
  F -->|enter read mode| K[Enter read mode]

  G --> E
  H --> E
  I --> E
  J --> E

  K --> L{Read mode command?}
  L -->|forwardX / fowardX| M[Advance reading position by X]
  L -->|backX| N[Move back by X]
  L -->|highlight| O[Highlight selection/segment]
  L -->|delete| P[Delete current article]
  L -->|archive| Q[Archive current article]
  L -->|exit read mode| E

  M --> K
  N --> K
  O --> K
  P --> K
  Q --> K

  E --> R{User leaves Daily room?}
  K --> R
  R -->|Yes| S[Terminate workflow]
  S --> T[Disconnect session and cleanup]
  R -->|No| E
```

## Text-Based Workflow

1. User enters the Daily room.
2. Launch trigger fires and starts the bot/app session.
3. App initializes and connects/authenticates to Instapaper.
4. Navigation mode is enabled.
5. In navigation mode, user can issue commands: `next`, `previous`, `delete`, `archive`, or enter read mode.
6. `next`, `previous`, `delete`, and `archive` keep the user in navigation mode.
7. Entering read mode switches into read mode command loop.
8. In read mode, user can issue commands: `forwardX` (or `fowardX`), `backX`, `highlight`, `delete`, `archive`, or `exit read mode`.
9. `forwardX`, `backX`, `highlight`, `delete`, and `archive` keep the user in read mode.
10. `exit read mode` returns to navigation mode.
11. If the user leaves the Daily room from either mode, the workflow terminates.
12. App disconnects and performs cleanup.

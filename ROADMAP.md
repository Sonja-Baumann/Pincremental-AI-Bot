# Improvement ideas

Prioritized from reliability to stronger play. None of these change the installed game files.

## Reliability

- Confirm the DevTools target belongs to Pincremental before attaching; currently the first page target is selected.
- Release both flippers in a `finally` block after every error or disconnect, not only on Ctrl+C.
- Reconnect cleanly if Steam restarts the game or the WebSocket drops.
- Detect Steam launch failures and show the command, timeout, and next action in a clearer message.
- Handle different Pincremental versions by checking for required page functions and selectors before play.

## Learning and scoring

- Log actions, ball trajectories, hits, record changes, and outcomes to a session file for review.
- Improve reward attribution so each flipper timing is credited only for score and saves that follow that action.
- Compare timing strategies over repeated runs, including confidence and sample counts, instead of relying only on a running average.
- Learn separate timing policies for left and right approaches and different incoming velocities.
- Track lifetime high score and score-per-minute separately from the current-run counters.

## Usability

- Add a small resizable status window showing the detected ball, velocity, chosen action, score, and learned timing values.
- Add pause/resume hotkeys and a clear emergency stop that releases both paddles.
- Make the Steam app ID, debugging port, and learning-data path configurable.
- Store settings and logs in the standard XDG config/data directories.
- Add a dry-run mode that observes the ball and reports suggested flipper actions without pressing them.

## Maintenance

- Add automated tests for DevTools messages, reward calculations, and the timing policy.
- Add a simulated Matter.js trajectory harness so strategy changes can be compared without risking a real run.
- Add CI for Python formatting, linting, type checks, and packaging.
- Document supported Steam runtime versions and test the launch arguments against game updates.

# Face liveness detector source for KYC

This folder belongs to the main `6-KYC` repository. Its source files, models and
documentation are versioned by the parent repository as ordinary files. The
folder has no separate Git repository or submodule setup.

## KYC application

The application's current liveness flow runs in:

- [LivenessStage.tsx](../Frontend/src/pages/verify/components/LivenessStage.tsx):
  browser camera capture and challenge guidance.
- [services/liveness.py](../src/kyc/services/liveness.py): session challenge and
  evidence processing.
- [liveness/active.py](../src/kyc/liveness/active.py): movement assessment, replay
  checks and identity continuity.

Start the KYC application using the [project setup instructions](../README.md).
Its runtime dependencies are in [pyproject.toml](../pyproject.toml) and
[requirements.lock](../requirements.lock).

The CNN and `main.py` in this folder are currently reference code for evaluating
passive anti-spoofing. `main.py` is the upstream standalone webcam demo, and its
historical `requirements.txt` belongs to that demo. The running KYC API does not
import this demo or use its score as a KYC decision.

## Upstream source and license

The original project documentation is preserved in
[UPSTREAM_README.md](UPSTREAM_README.md). It attributes the project to Prem Kumar
and links to [Prem95/face-liveness-detector](https://github.com/Prem95/face-liveness-detector).
The imported revision is `d0e33110d76cf03e9341b4acf6143f24e5a31c24`.

The upstream [MIT license](LICENSE), copyright notice, source and model files
are retained. Make and commit local changes from the `6-KYC` repository root.

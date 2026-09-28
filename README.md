# NanaTools

Windows desktop app (Python, CustomTkinter) that groups four tools in one window:

- Video downloader based on yt-dlp: queue, HLS, subtitles, resume and cancel.
- File organizer for Downloads and Desktop, with a preview and undo.
- Batch image converter (format and size).
- Japanese text analyzer: segmentation and JMdict lookups, reading the DNAreader
  dictionary in read-only mode.

The tools share one core for background jobs, settings and logging, and the
core has no UI dependency, so it is tested without a display.

## Run

```bash
pip install -r requirements.txt
python app.py
```

Tests: `python -m unittest discover -s tests -t .`

## Stack

Python 3.14, CustomTkinter, yt-dlp, Pillow.

## License

All rights reserved. Published as a portfolio for viewing and evaluation only;
reuse or redistribution is not permitted. See [LICENSE](LICENSE).

# Third-party notices

The MIT license at the repository root applies to the author's code. It does not
relicense external dependencies, financial filings or third-party datasets.

Runtime packages are installed separately. The tested distributions report:

| Package | License metadata |
|---|---|
| Pydantic | MIT |
| pypdfium2 | BSD-3-Clause, Apache-2.0 and dependency notices |
| Pillow | MIT-CMU |

Keep the notices supplied by those distributions. In particular, PDFium carries
additional component notices within pypdfium2's installed license directory.

Optional historical HTML/TEDS code refers to PubTabNet metric code at commit
`8ffde9024bd331f5a61c3c549fdf30dd3c3e43a5`. Its metric identifies Copyright 2020 IBM
and Apache-2.0; its dataset license is separate. Those third-party source files are
not bundled in this public code candidate and have not been placed under MIT. The
unmodified originals and existing notices remain in the separate research archive.
HTML/TEDS is not the primary full50 CSV metric or an advertised runnable release feature.

No real filing images, PDFs, raw paid model outputs or external reference dataset
is distributed in this code repository. Source-data redistribution needs its own
rights/provenance review.

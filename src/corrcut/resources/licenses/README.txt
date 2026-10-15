License texts shipped with CorrCUT
==================================

These files are the full license texts for third-party software redistributed
with the Windows application. The index and written offer for corresponding
source are in ../THIRD_PARTY_NOTICES.txt (and the copy next to the installed
application).

GPL-2.0.txt       GNU General Public License, version 2
GPL-3.0.txt       GNU General Public License, version 3
LGPL-2.1.txt      GNU Lesser General Public License, version 2.1
LGPL-3.0.txt      GNU Lesser General Public License, version 3
Apache-2.0.txt    Apache License, version 2.0
OFL-1.1.txt       SIL Open Font License, version 1.1
Python-PSF.txt    Python Software Foundation license (embeddable CPython)

Qt / PySide6 also ship additional third-party notices under:

  python\Lib\site-packages\PySide6-*.dist-info\licenses\

The Windows release script removes unused Qt WebEngine and Qt PDF wheels
before packaging, so Chromium WebEngine notices are not redistributed.

Individual Python packages keep their own LICENSE files under:

  python\Lib\site-packages\*-dist-info\

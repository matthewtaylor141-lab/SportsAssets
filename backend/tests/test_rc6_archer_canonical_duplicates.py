"""CAPITAL-CRITICAL: THE ECONOMIC-DUPLICATE RULE ON THE CANONICAL IDENTITY OF
OBSERVED LIQUIDITY -- THE VENUE READ, NOT THE OBSERVATION ROW (RC6 archer-
lifecycle).

Production research runs 37874201361 / 37874710852 / 37875517525
(2026-10-09 02:21-02:38Z, release 69a8a07e): every fill of the P0 rule's 18
groups carries its order's account, session, group, contract, side,
direction and role, one account, one venue source, one position per order --
no group mixes accounts, venues, contracts, sides or position groups. What
the row-level rules (P0, then RC6 archer-dups) missed: a read shared with
another caller is recorded again with its ORIGINAL receipt instant, so ONE
book is several paper_book_observations rows, while the consumed-liquidity
ledger is keyed by the row. Before the seen-crossing fix the resting step met
every copy as a new book.

Proven here, on production's rows verbatim and on a real ledger:

  * the 18 P0 groups re-verified one by one: 12 duplicates, 6 not (84a543eb,
    which archer-dups called distinct levels, filled on a COPY of the read
    the queue ahead had taken; bd296caf re-sold a still-displayed level on a
    later read); every duplicate HISTORICAL, none current, and the six
    after-fix cross-read refills all within new liquidity;
  * the same rows moved after the fix are all CURRENT (the window labels
    history, it never hides a duplicate);
  * a same-read copy with a DIFFERENT size (no P0 group) and a refill on a
    LATER read (a different instant) -- both invisible to the row-level
    rules -- are named, HISTORICAL before the fix and CURRENT after it; a
    level that left the book in between is new liquidity, not a duplicate;
  * the fixed simulator on the copy shape takes nothing from the copy;
  * the receipt never claims a current count from a truncated read.
"""
from __future__ import annotations

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_reconciliation as REC
from sportsassets import bettor_paper_simulator as SIM

from tests import paper_harness as H
from tests import test_paper_economic_duplicate_rule as DUP

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

#: research run 37875517525 (2026-10-09 02:38Z): the receipt's own
#: CANONICAL_FILLS_SQL over production -- the rows of the P0 groups' 11
#: orders and every judged row filled after the fix. (position index into
#: PRODUCTION_POSITIONS: group, market, held side; every row a RESTING
#: STANDING_PROTECTION SELL of paper_acct_main.) Row fields: order, position,
#: obs, first row of its read, previous examined row, wire, qty, price,
#: filled epoch, shown at the wire on the row, shown on the previous row,
#: fills of the order at the wire, fills in its P0 group.
PRODUCTION_POSITIONS = (
    ("papercggrp:953535d3fe3673ec87a14826", "aec-npb-clm-ssl-2026-10-05", "SHORT"),
    ("papercggrp:dd96b640671d655c10554997", "atc-brb-acg-afc-2026-10-03-acg", "LONG"),
    ("paperexpgrp:00c8dfe1f31dd1a3f6122072", "aec-npb-clm-ssl-2026-10-06", "SHORT"),
    ("paperexpgrp:1a27369ede2c3f8db290417d", "atc-intf-jor-ven-2026-10-06-jor", "LONG"),
    ("paperexpgrp:41ad9b46fe28d6add40bd564", "atc-brb-vln-cui-2026-10-07-vln", "SHORT"),
    ("paperexpgrp:43c31ba5a683616e1bdf3659", "aec-kbo-khi-kwt-2026-10-06", "SHORT"),
    ("paperexpgrp:55be1f6cb47c55f648c78b2b", "atc-intf-rwa-ken-2026-10-05-rwa", "SHORT"),
    ("paperexpgrp:73296cf5e61c08e0a67e450f", "atc-brb-csc-cri-2026-10-08-csc", "LONG"),
    ("paperexpgrp:9edb6e885da69dff9e022a0a", "aec-kbo-kti-sla-2026-10-06", "SHORT"),
    ("paperexpgrp:a51a5f893f337758a2e88174", "atc-brb-ber-crb-2026-10-02-ber", "LONG"),
    ("paperexpgrp:c2d980d0163ce61a219993a1", "atc-u19f-wal-nir-2026-10-05-wal", "SHORT"),
    ("paperexpgrp:cc66c2c366c8aef8a6e7fb9f", "atc-cnl-mtq-slv-2026-10-05-mtq", "SHORT"),
    ("paperexpgrp:d360f05737f3a24551b00c8c", "atc-unl-mne-arm-2026-10-05-mne", "SHORT"),
    ("paperexpgrp:ddfa9b34b385c60bf3d3c8f0", "aec-kbo-lgo-dbo-2026-10-06", "LONG"),
    ("paperexpgrp:fa0417c313e29eaf8ca33056", "atc-afcq-ang-maw-2026-10-06-ang", "LONG"),
    ("papergrp:425640007467eeae355634ae", "atc-cnl-mtq-slv-2026-10-05-mtq", "SHORT"),
    ("papergrp:8e24a638272a07d975a7783d", "aec-mlb-mil-sd-2026-10-06", "SHORT"),
    ("papergrp:d3269008fbca6a87b5e75225", "aec-kbo-lgo-dbo-2026-10-06", "SHORT"),
    ("papergrp:e68836877c3a1a448392cee0", "aec-kbo-khi-kwt-2026-10-06", "SHORT"),
    ("papergrp:eab5a64434fae7460fa21be3", "atc-unl-ukr-hun-2026-10-05-ukr", "SHORT"),
    ("papergrp:fe62c53bd2283dd400c17cea", "atc-afcq-ang-maw-2026-10-06-ang", "LONG"),
)
PRODUCTION_ROWS = (
    ("0835d3b3673f6e56a3bbad85", 7, 143703, 143703, 143701, 0.86, 60,
     0.46, 1791505408.848672, 288, 86, 1, 1),
    ("531a015da49edbf8625f49c2", 4, 125999, 125999, 125992, 0.35, 641,
     0.64, 1791420253.782163, 3649.0, 458.0, 2, 1),
    ("2ad3582a6f9e65bc24d96e69", 16, 95438, 95438, 95389, 0.38, 185.01,
     0.61, 1791340272.164168, 425.01, 240.0, 1, 1),
    ("34cf2d72a05d3579b518dc55", 14, 84795, 84795, 84347, 0.86, 1351,
     0.76, 1791314999.485896, 3804.0, 306.97, 1, 1),
    ("4f0f78616ef3382507b9511c", 20, 84795, 84795, 84347, 0.86, 495.48,
     0.76, 1791314999.485896, 3804.0, 306.97, 1, 1),
    ("bd97a093155513748c0ebd7f", 8, 71487, 71487, 71471, 0.32, 22.75,
     0.61, 1791288647.857416, 905.0, 305.0, 1, 1),
    ("0c070b03c47504e9e7f24362", 13, 69902, 69902, 69799, 0.48, 251.3,
     0.46, 1791285805.629167, 303.5, 30.0, 1, 1),
    ("0c070b03c47504e9e7f24362", 13, 69796, 69796, 69759, 0.47, 104.4,
     0.46, 1791285743.478474, 203.0, 98.6, 1, 1),
    ("3a7639f4923ec193a47faf89", 17, 68189, 68189, 68161, 0.32, 242.1,
     0.67, 1791283164.718956, 797.59, 391.17, 1, 1),
    ("6ec0086e9379a181ffe0a76d", 8, 67535, 67535, 67531, 0.37, 1,
     0.61, 1791282204.489129, 101.0, 100.0, 2, 1),
    ("6ec0086e9379a181ffe0a76d", 8, 67535, 67535, 67531, 0.38, 8,
     0.61, 1791282204.489129, 48.0, 40.0, 2, 1),
    ("f35bb6ee714d610ca664d33f", 18, 67419, 67419, 67417, 0.18, 100,
     0.75, 1791281967.964966, 100.0, None, 1, 2),
    ("f35bb6ee714d610ca664d33f", 18, 67419, 67419, 67417, 0.19, 100,
     0.75, 1791281967.964966, 100.0, None, 1, 2),
    ("2bf0520c2406981735a94ee0", 5, 67293, 67293, 67255, 0.24, 102.7,
     0.74, 1791281721.525017, 123.54, 20.84, 2, 1),
    ("d3c8617af5f73579b9f69271", 2, 65306, 65306, 65292, 0.23, 70,
     0.76, 1791278544.985983, 380.45, 310.45, 2, 1),
    ("c07c30f51e26a4d10581de67", 15, 47863, 47863, None, 0.29, 6,
     0.67, 1791238832.889784, 6.0, None, 1, 2),
    ("c07c30f51e26a4d10581de67", 15, 47863, 47863, None, 0.3, 6,
     0.67, 1791238832.889784, 6.0, None, 1, 2),
    ("96afc3f9a9592192d6bcbba6", 11, 47825, 47825, None, 0.31, 323.49,
     0.66, 1791238675.651934, 323.49, None, 3, 3),
    ("96afc3f9a9592192d6bcbba6", 11, 47826, 47825, 47825, 0.31, 323.49,
     0.66, 1791238675.651934, 323.49, 323.49, 3, 3),
    ("96afc3f9a9592192d6bcbba6", 11, 47828, 47825, 47826, 0.31, 323.49,
     0.66, 1791238675.651934, 323.49, 323.49, 3, 3),
    ("96afc3f9a9592192d6bcbba6", 11, 47825, 47825, None, 0.32, 104,
     0.66, 1791238675.651934, 104.0, None, 3, 3),
    ("96afc3f9a9592192d6bcbba6", 11, 47826, 47825, 47825, 0.32, 104,
     0.66, 1791238675.651934, 104.0, 104.0, 3, 3),
    ("96afc3f9a9592192d6bcbba6", 11, 47828, 47825, 47826, 0.32, 104,
     0.66, 1791238675.651934, 104.0, 104.0, 3, 3),
    ("96afc3f9a9592192d6bcbba6", 11, 47825, 47825, None, 0.33, 5,
     0.66, 1791238675.651934, 5.0, None, 3, 3),
    ("96afc3f9a9592192d6bcbba6", 11, 47826, 47825, 47825, 0.33, 5,
     0.66, 1791238675.651934, 5.0, 5.0, 3, 3),
    ("96afc3f9a9592192d6bcbba6", 11, 47828, 47825, 47826, 0.33, 5,
     0.66, 1791238675.651934, 5.0, 5.0, 3, 3),
    ("62c73a8f60f8ec214dc3f0a5", 12, 46247, 46247, 46246, 0.42, 28.78,
     0.56, 1791230963.878197, 474.33, 474.33, 3, 1),
    ("62c73a8f60f8ec214dc3f0a5", 12, 46248, 46247, 46247, 0.42, 474.33,
     0.56, 1791230963.878197, 474.33, 474.33, 3, 1),
    ("62c73a8f60f8ec214dc3f0a5", 12, 46250, 46247, 46248, 0.42, 30.89,
     0.56, 1791230963.878197, 474.33, 474.33, 3, 1),
    ("62c73a8f60f8ec214dc3f0a5", 12, 46247, 46247, 46246, 0.43, 3,
     0.56, 1791230963.878197, 3.0, 3.0, 2, 2),
    ("62c73a8f60f8ec214dc3f0a5", 12, 46248, 46247, 46247, 0.43, 3,
     0.56, 1791230963.878197, 3.0, 3.0, 2, 2),
    ("84a543eb290cb7be9e104ac0", 19, 46173, 46172, 46172, 0.18, 1,
     0.69, 1791230071.268251, 1.0, 1.0, 1, 4),
    ("84a543eb290cb7be9e104ac0", 19, 46173, 46172, 46172, 0.19, 224,
     0.69, 1791230071.268251, 224.0, 224.0, 1, 1),
    ("84a543eb290cb7be9e104ac0", 19, 46173, 46172, 46172, 0.2, 1102.48,
     0.69, 1791230071.268251, 2605.0, 2605.0, 2, 1),
    ("84a543eb290cb7be9e104ac0", 19, 46172, 46172, 46069, 0.21, 1,
     0.69, 1791230071.268251, 1.0, None, 1, 4),
    ("84a543eb290cb7be9e104ac0", 19, 46172, 46172, 46069, 0.22, 1,
     0.69, 1791230071.268251, 1.0, None, 1, 4),
    ("84a543eb290cb7be9e104ac0", 19, 46172, 46172, 46069, 0.23, 1,
     0.69, 1791230071.268251, 1.0, None, 1, 4),
    ("94bf0e34989e6f663a5ebc1d", 6, 44825, 44825, None, 0.1, 142,
     0.83, 1791223012.129258, 142.0, None, 6, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44826, 44825, 44825, 0.1, 142,
     0.83, 1791223012.129258, 142.0, 142.0, 6, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44827, 44825, 44826, 0.1, 142,
     0.83, 1791223012.129258, 142.0, 142.0, 6, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44828, 44825, 44827, 0.1, 142,
     0.83, 1791223012.129258, 142.0, 142.0, 6, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44829, 44829, 44828, 0.1, 142,
     0.83, 1791223012.129258, 142.0, 142.0, 6, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44830, 44830, 44829, 0.1, 34,
     0.83, 1791223012.129258, 142.0, 142.0, 6, 1),
    ("94bf0e34989e6f663a5ebc1d", 6, 44825, 44825, None, 0.11, 36,
     0.83, 1791223012.129258, 36.0, None, 5, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44826, 44825, 44825, 0.11, 36,
     0.83, 1791223012.129258, 36.0, 36.0, 5, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44827, 44825, 44826, 0.11, 36,
     0.83, 1791223012.129258, 36.0, 36.0, 5, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44828, 44825, 44827, 0.11, 36,
     0.83, 1791223012.129258, 36.0, 36.0, 5, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44829, 44829, 44828, 0.11, 36,
     0.83, 1791223012.129258, 36.0, 36.0, 5, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44825, 44825, None, 0.12, 15,
     0.83, 1791223012.129258, 15.0, None, 5, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44826, 44825, 44825, 0.12, 15,
     0.83, 1791223012.129258, 15.0, 15.0, 5, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44827, 44825, 44826, 0.12, 15,
     0.83, 1791223012.129258, 15.0, 15.0, 5, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44828, 44825, 44827, 0.12, 15,
     0.83, 1791223012.129258, 15.0, 15.0, 5, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44829, 44829, 44828, 0.12, 15,
     0.83, 1791223012.129258, 15.0, 15.0, 5, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44825, 44825, None, 0.13, 7,
     0.83, 1791223012.129258, 7.0, None, 5, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44826, 44825, 44825, 0.13, 7,
     0.83, 1791223012.129258, 7.0, 7.0, 5, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44827, 44825, 44826, 0.13, 7,
     0.83, 1791223012.129258, 7.0, 7.0, 5, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44828, 44825, 44827, 0.13, 7,
     0.83, 1791223012.129258, 7.0, 7.0, 5, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44829, 44829, 44828, 0.13, 7,
     0.83, 1791223012.129258, 7.0, 7.0, 5, 5),
    ("94bf0e34989e6f663a5ebc1d", 6, 44825, 44825, None, 0.14, 1,
     0.83, 1791223012.129258, 1.0, None, 5, 15),
    ("94bf0e34989e6f663a5ebc1d", 6, 44826, 44825, 44825, 0.14, 1,
     0.83, 1791223012.129258, 1.0, 1.0, 5, 15),
    ("94bf0e34989e6f663a5ebc1d", 6, 44827, 44825, 44826, 0.14, 1,
     0.83, 1791223012.129258, 1.0, 1.0, 5, 15),
    ("94bf0e34989e6f663a5ebc1d", 6, 44828, 44825, 44827, 0.14, 1,
     0.83, 1791223012.129258, 1.0, 1.0, 5, 15),
    ("94bf0e34989e6f663a5ebc1d", 6, 44829, 44829, 44828, 0.14, 1,
     0.83, 1791223012.129258, 1.0, 1.0, 5, 15),
    ("94bf0e34989e6f663a5ebc1d", 6, 44825, 44825, None, 0.15, 1,
     0.83, 1791223012.129258, 1.0, None, 5, 15),
    ("94bf0e34989e6f663a5ebc1d", 6, 44826, 44825, 44825, 0.15, 1,
     0.83, 1791223012.129258, 1.0, 1.0, 5, 15),
    ("94bf0e34989e6f663a5ebc1d", 6, 44827, 44825, 44826, 0.15, 1,
     0.83, 1791223012.129258, 1.0, 1.0, 5, 15),
    ("94bf0e34989e6f663a5ebc1d", 6, 44828, 44825, 44827, 0.15, 1,
     0.83, 1791223012.129258, 1.0, 1.0, 5, 15),
    ("94bf0e34989e6f663a5ebc1d", 6, 44829, 44829, 44828, 0.15, 1,
     0.83, 1791223012.129258, 1.0, 1.0, 5, 15),
    ("94bf0e34989e6f663a5ebc1d", 6, 44825, 44825, None, 0.16, 1,
     0.83, 1791223012.129258, 1.0, None, 5, 15),
    ("94bf0e34989e6f663a5ebc1d", 6, 44826, 44825, 44825, 0.16, 1,
     0.83, 1791223012.129258, 1.0, 1.0, 5, 15),
    ("94bf0e34989e6f663a5ebc1d", 6, 44827, 44825, 44826, 0.16, 1,
     0.83, 1791223012.129258, 1.0, 1.0, 5, 15),
    ("94bf0e34989e6f663a5ebc1d", 6, 44828, 44825, 44827, 0.16, 1,
     0.83, 1791223012.129258, 1.0, 1.0, 5, 15),
    ("94bf0e34989e6f663a5ebc1d", 6, 44829, 44829, 44828, 0.16, 1,
     0.83, 1791223012.129258, 1.0, 1.0, 5, 15),
    ("bd296cafc26e5fd22d01db7d", 3, 40623, 40623, 40284, 0.41, 539,
     0.4, 1791209254.418665, 539.0, 539.0, 5, 4),
    ("bd296cafc26e5fd22d01db7d", 3, 40624, 40623, 40623, 0.41, 539,
     0.4, 1791209254.418665, 539.0, 539.0, 5, 4),
    ("bd296cafc26e5fd22d01db7d", 3, 40625, 40623, 40624, 0.41, 539,
     0.4, 1791209254.418665, 539.0, 539.0, 5, 4),
    ("bd296cafc26e5fd22d01db7d", 3, 40626, 40623, 40625, 0.41, 539,
     0.4, 1791209254.418665, 539.0, 539.0, 5, 4),
    ("5c9f045a7a597ae9359cebf4", 10, 37261, 37261, 37086, 0.06, 5,
     0.42, 1791197485.412118, 5.0, None, 1, 4),
    ("5c9f045a7a597ae9359cebf4", 10, 37261, 37261, 37086, 0.07, 5,
     0.42, 1791197485.412118, 5.0, None, 1, 4),
    ("5c9f045a7a597ae9359cebf4", 10, 37261, 37261, 37086, 0.09, 5,
     0.42, 1791197485.412118, 5.0, None, 1, 4),
    ("5c9f045a7a597ae9359cebf4", 10, 37261, 37261, 37086, 0.1, 5,
     0.42, 1791197485.412118, 5.0, None, 1, 4),
    ("5c9f045a7a597ae9359cebf4", 10, 37261, 37261, 37086, 0.13, 1,
     0.42, 1791197485.412118, 1.0, None, 1, 2),
    ("5c9f045a7a597ae9359cebf4", 10, 37261, 37261, 37086, 0.14, 1,
     0.42, 1791197485.412118, 1.0, None, 1, 2),
    ("dd954687a4007847e1ebc270", 0, 36939, 36939, 36924, 0.47, 105.81,
     0.52, 1791196583.63641, 105.81, None, 3, 2),
    ("dd954687a4007847e1ebc270", 0, 36940, 36939, 36939, 0.47, 105.81,
     0.52, 1791196583.63641, 105.81, 105.81, 3, 2),
    ("dd954687a4007847e1ebc270", 0, 36941, 36939, 36940, 0.47, 65.38,
     0.52, 1791196583.63641, 105.81, 105.81, 3, 1),
    ("49d199d5412c3a0e396a39f0", 1, 19303, 19303, 19284, 0.96, 20.8,
     0.86, 1791058634.953595, 4058.65, 5196.7, 2, 1),
    ("49d199d5412c3a0e396a39f0", 1, 19284, 19284, 19258, 0.93, 100,
     0.86, 1791058519.762839, 100.0, None, 1, 3),
    ("49d199d5412c3a0e396a39f0", 1, 19284, 19284, 19258, 0.94, 100,
     0.86, 1791058519.762839, 100.0, None, 1, 3),
    ("49d199d5412c3a0e396a39f0", 1, 19284, 19284, 19258, 0.95, 100,
     0.86, 1791058519.762839, 100.0, None, 1, 3),
    ("57612207b7265210aec4681c", 9, 10434, 10434, 10418, 0.59, 3,
     0.42, 1790985278.020519, 3.0, None, 1, 2),
    ("57612207b7265210aec4681c", 9, 10434, 10434, 10418, 0.63, 3,
     0.42, 1790985278.020519, 3.0, None, 1, 2),
)

#: the P0 groups' 11 orders
P0_ORDERS = ("f35bb6ee714d610ca664d33f", "c07c30f51e26a4d10581de67",
             "96afc3f9a9592192d6bcbba6", "62c73a8f60f8ec214dc3f0a5",
             "84a543eb290cb7be9e104ac0", "94bf0e34989e6f663a5ebc1d",
             "bd296cafc26e5fd22d01db7d", "5c9f045a7a597ae9359cebf4",
             "dd954687a4007847e1ebc270", "49d199d5412c3a0e396a39f0",
             "57612207b7265210aec4681c")


def _rows(shift_s: float = 0.0, *, rows=PRODUCTION_ROWS) -> list:
    out = []
    for (oid, pi, obs, first, prev, wire, qty, price, ep, shown, pshown,
         lf, fn) in rows:
        gid, slug, side = PRODUCTION_POSITIONS[pi]
        t = ep + shift_s
        key = "paperord:%s:obs%d:%s" % (oid, obs, SIM._wk(wire))
        out.append({"fill_id": L.fill_id_for(key),
                    "order_id": "paperord:" + oid,
                    "account_id": "paper_acct_main", "group_id": gid,
                    "us_market_slug": slug, "holding_side": side,
                    "direction": "SELL", "role": "STANDING_PROTECTION",
                    "order_type": "RESTING", "qty": qty, "price": price,
                    "wire_price": wire, "book_obs_id": obs,
                    "filled_at": "%.6f" % t, "filled_epoch": t,
                    "read_first_obs": first, "prev_obs": prev,
                    "shown": shown, "prev_shown": pshown,
                    "displayed_recorded": shown, "level_fills": lf,
                    "former_n": fn})
    return out


# ═════════════════════════════════════════════════════════════════════
# PURE, ON PRODUCTION'S ROWS
# ═════════════════════════════════════════════════════════════════════

def test_the_18_p0_groups_reverified_one_by_one():
    got = REC.canonical_duplicates(_rows())
    assert got["former_rule_groups"] == 18
    assert got["former_rule_groups_with_a_duplicate"] == 12
    v = {(r["order_id"][9:17], r["qty"]): (r["verdict"], r["duplicate_qty"],
                                            r["kinds"])
         for r in got["former_rule"]}
    copy, still = REC.K_COPY, REC.K_STILL
    assert v == {
        # NOT duplicates: distinct levels, each on the first row of a read
        # the order had not seen it on
        ("f35bb6ee", 100.0): ("NOT_A_DUPLICATE", 0.0, []),
        ("c07c30f5", 6.0): ("NOT_A_DUPLICATE", 0.0, []),
        ("5c9f045a", 5.0): ("NOT_A_DUPLICATE", 0.0, []),
        ("5c9f045a", 1.0): ("NOT_A_DUPLICATE", 0.0, []),
        ("49d199d5", 100.0): ("NOT_A_DUPLICATE", 0.0, []),
        ("57612207", 3.0): ("NOT_A_DUPLICATE", 0.0, []),
        # duplicates: rows 47826 / 47828 are copies of the read of 47825
        ("96afc3f9", 323.49): ("DUPLICATE", 646.98, [copy]),
        ("96afc3f9", 104.0): ("DUPLICATE", 208.0, [copy]),
        ("96afc3f9", 5.0): ("DUPLICATE", 10.0, [copy]),
        # 46247 shows row 46246's sizes again, 46248 is its copy
        ("62c73a8f", 3.0): ("DUPLICATE", 6.0, [copy, still]),
        # called distinct levels by archer-dups: the 0.18 lot sits on row
        # 46173, a copy of the read of 46172 whose size the queue took
        ("84a543eb", 1.0): ("DUPLICATE", 1.0, [copy]),
        ("94bf0e34", 142.0): ("DUPLICATE", 568.0, [copy, still]),
        ("94bf0e34", 36.0): ("DUPLICATE", 144.0, [copy, still]),
        ("94bf0e34", 15.0): ("DUPLICATE", 60.0, [copy, still]),
        ("94bf0e34", 7.0): ("DUPLICATE", 28.0, [copy, still]),
        ("94bf0e34", 1.0): ("DUPLICATE", 12.0, [copy, still]),
        # 0.41 x 539 shown at 13:42 and again on the first row of the 14:06
        # read, then three copies of that read
        ("bd296caf", 539.0): ("DUPLICATE", 2156.0, [copy, still]),
        ("dd954687", 105.81): ("DUPLICATE", 105.81, [copy]),
    }
    assert {d["order_id"][9:17] for d in got["distinct_levels"]} == {
        "f35bb6ee", "c07c30f5", "5c9f045a", "49d199d5", "57612207"}
    assert len(got["distinct_levels"]) == 6
    assert all(d["label"] == REC.L_DISTINCT for d in got["distinct_levels"])


def test_every_duplicate_is_historical_and_the_orders_totals_are_named():
    got = REC.canonical_duplicates(_rows())
    assert got["current"] == []
    by_order: dict = {}
    for g in got["historical"]:
        assert g["label"] == REC.L_HISTORICAL
        assert g["window"] == "BEFORE_PRODUCER_FIX"
        assert g["filled_epoch"] < REC.PRODUCER_FIX_EFFECTIVE_AT
        by_order[g["order_id"][9:17]] = round(
            by_order.get(g["order_id"][9:17], 0.0) + g["extra_qty"], 6)
    # what each order sold beyond the liquidity it was offered
    assert by_order == {"96afc3f9": 864.98, "62c73a8f": 540.0,
                        "84a543eb": 1327.48, "94bf0e34": 846.0,
                        "bd296caf": 2156.0, "dd954687": 171.19,
                        "49d199d5": 20.8}
    k = got["fills_by_kind"]["BEFORE_PRODUCER_FIX"]
    assert set(k) == {REC.K_COPY, REC.K_STILL}
    # every affected position is named with what its sales carried
    pos = {p["position_key"]: p for p in got["positions"]}
    p84 = pos["paperpos:paper_acct_main:papergrp:eab5a64434fae7460fa21be3:"
              "atc-unl-ukr-hun-2026-10-05-ukr:SHORT"]
    assert p84["duplicate_qty_sold"] == pytest.approx(1327.48)
    assert p84["duplicate_qty_bought"] == 0.0
    assert p84["windows"] == ["BEFORE_PRODUCER_FIX"]


def test_the_after_fix_cross_read_fills_took_only_new_liquidity():
    """The six levels one order filled on two different reads after the fix
    (d3c8617a, 2bf0520c, 6ec0086e x2, c4abc38a, 531a015d) and every other
    judged after-fix row: each fill <= what its row showed above what the
    row examined before it showed (c4abc38a's level had left the book)."""
    after = [r for r in _rows()
             if r["filled_epoch"] >= REC.PRODUCER_FIX_EFFECTIVE_AT]
    assert len(after) == 15
    for r in after:
        assert REC.judge_fill(r)["kind"] is None, r
    assert REC.canonical_duplicates(after)["current"] == []


def test_the_same_rows_after_the_fix_are_all_current():
    shift = REC.PRODUCER_FIX_EFFECTIVE_AT + 86400.0 - min(
        r[8] for r in PRODUCTION_ROWS)
    before = [r for r in PRODUCTION_ROWS
              if r[8] < REC.PRODUCER_FIX_EFFECTIVE_AT]
    got = REC.canonical_duplicates(_rows(shift, rows=before))
    assert got["historical"] == []
    assert all(g["label"] == REC.L_SUSPECT for g in got["current"])
    assert round(sum(g["extra_qty"] for g in got["current"]), 6) == \
        pytest.approx(864.98 + 540.0 + 1327.48 + 846.0 + 2156.0 + 171.19
                      + 20.8)


def test_judge_fill_rules():
    base = {"qty": 10.0, "book_obs_id": 7, "read_first_obs": 7,
            "order_type": "RESTING", "prev_obs": 6}
    # a copy row: the whole fill
    j = REC.judge_fill(dict(base, book_obs_id=8))
    assert (j["kind"], j["dup_qty"]) == (REC.K_COPY, 10.0)
    # the level grew from 5 to 12: 7 new, 3 still displayed
    j = REC.judge_fill(dict(base, shown=12.0, prev_shown=5.0))
    assert (j["kind"], j["dup_qty"], j["new_liquidity"]) == (
        REC.K_STILL, 3.0, 7.0)
    # grew enough: not a duplicate
    assert REC.judge_fill(dict(base, shown=20.0, prev_shown=5.0))["kind"] \
        is None
    # the previous row did not show the level: all new
    assert REC.judge_fill(dict(base, shown=10.0, prev_shown=None))["kind"] \
        is None
    # the row's own size unreadable: falls back to the simulator's record,
    # and when neither is known the cap is 0 (fail closed)
    j = REC.judge_fill(dict(base, shown=None, displayed_recorded=None,
                            prev_shown=5.0))
    assert (j["kind"], j["dup_qty"]) == (REC.K_STILL, 10.0)
    # a marketable fill has no resting memory; only the copy rule applies
    assert REC.judge_fill(dict(base, order_type="MARKETABLE", shown=10.0,
                               prev_shown=10.0))["kind"] is None
    assert REC.canonical_duplicates([])["current"] == []


# ═════════════════════════════════════════════════════════════════════
# A REAL LEDGER (scratch test database; no network, no real order)
# ═════════════════════════════════════════════════════════════════════

BEFORE = REC.PRODUCER_FIX_EFFECTIVE_AT - 86400.0
AFTER = REC.PRODUCER_FIX_EFFECTIVE_AT + 3600.0


async def _old_take(conn, oid: str, obs_id: int, observed_at: float, *,
                    wire: float, displayed: float, take: float,
                    now: float) -> None:
    """THE PRE-FIX PRODUCER'S TAKE, through its own writer: one level of
    one row, with no memory of what the order was already offered."""
    async with conn.transaction():
        acct = await conn.fetchval(
            "SELECT account_id FROM paper_orders WHERE order_id=$1", oid)
        await L._lock(conn, acct)
        o = await conn.fetchrow("SELECT * FROM paper_orders WHERE "
                                "order_id=$1", oid)
        await SIM._apply_takes(
            conn, o, takes=[{"wire": wire, "price": wire, "qty": displayed,
                             "take": take}],
            obs={"obs_id": obs_id, "observed_at": observed_at},
            basis=SIM.BASIS_CROSS, now=now, fee_fn=H.zero_fee,
            evidence={"emulates": "PRE_FIX_PRODUCER"})


async def _counts(conn, account_id, at):
    rc = await REC.receipt(conn, account_id, now=at)
    assert rc["status"] == "OK", rc
    c = rc["counts"]
    return rc, (c["economic_duplicate_suspect_groups"],
                c["historical_economic_duplicate_groups"],
                c["economic_duplicate_suspect_extra_qty"],
                c["historical_economic_duplicate_extra_qty"])


@pg
@pytest.mark.parametrize("when", ["BEFORE", "AFTER"])
async def test_a_copy_of_one_read_with_another_size_is_a_duplicate(when):
    """Production 84a543eb (2026-10-05 19:54Z): ONE read recorded as rows
    46172 and 46173; on the first row the queue ahead took the 0.20 / 0.19
    sizes and the order 1 lot each at 0.21-0.23; on the copy the old step
    filled 224 at 0.19 again -- no other fill of that size, so the P0 and
    archer-dups rules never saw it."""
    t0 = BEFORE if when == "BEFORE" else AFTER
    conn = await H.connect()
    try:
        a, slug, _g, oid = await DUP._protected_long(conn, "rc6copy",
                                                     qty=300, at=t0)
        book = dict(bids=[(0.95, 1), (0.94, 224)])
        r1 = await H.observe(conn, slug, t0 + 10, **book)
        r2 = await H.observe(conn, slug, t0 + 10, **book)  # the same read
        await _old_take(conn, oid, r1, t0 + 10, wire=0.95, displayed=1,
                        take=1.0, now=t0 + 15)
        await _old_take(conn, oid, r2, t0 + 10, wire=0.94, displayed=224,
                        take=224.0, now=t0 + 15)
        rc, counts = await _counts(conn, a["account_id"], t0 + 30)
        if when == "BEFORE":
            assert counts == (0, 1, 0.0, pytest.approx(224.0))
            (g,) = rc["sections"]["historical_economic_duplicates"]
        else:
            assert counts == (1, 0, pytest.approx(224.0), 0.0)
            (g,) = rc["sections"]["economic_duplicate_suspects"]
        assert g["order_id"] == oid and g["kinds"] == [REC.K_COPY]
        (d,) = g["duplicate_fills"]
        assert d["book_obs_id"] == r2 and d["read_first_obs"] == r1
        # the row-level rule had no group to name
        assert rc["counts"]["former_rule_groups"] == 0
        (p,) = rc["sections"]["positions_with_economic_duplicates"]
        assert p["duplicate_qty_sold"] == pytest.approx(224.0)
        # nothing rewritten: both fills stand
        assert await conn.fetchval("SELECT count(*) FROM paper_fills WHERE "
                                   " order_id=$1", oid) == 2
    finally:
        await conn.close()


@pg
@pytest.mark.parametrize("when", ["BEFORE", "AFTER"])
async def test_a_level_still_displayed_on_a_later_read_is_a_duplicate(when):
    """Production bd296caf (2026-10-05): 0.41 x 539 sold at 13:42 from one
    read, and again at 14:07 from the first row of a later read that still
    showed 539 (an unreadable row between them changes nothing). Two
    instants: the P0 and archer-dups rules needed one."""
    t0 = BEFORE if when == "BEFORE" else AFTER
    conn = await H.connect()
    try:
        a, slug, _g, oid = await DUP._protected_long(conn, "rc6still",
                                                     qty=300, at=t0)
        ra = await H.observe(conn, slug, t0 + 10, bids=[(0.95, 100)])
        await _old_take(conn, oid, ra, t0 + 10, wire=0.95, displayed=100,
                        take=100.0, now=t0 + 11)
        await SIM.record_book(conn, slug=slug, read={
            "error": "VENUE_COOLDOWN", "observed_at": t0 + 40},
            source="TEST_FIXTURE_SYNTHETIC_BOOK", read_basis="TEST")
        rb = await H.observe(conn, slug, t0 + 60, bids=[(0.95, 150)])
        await _old_take(conn, oid, rb, t0 + 60, wire=0.95, displayed=150,
                        take=150.0, now=t0 + 61)
        rc, counts = await _counts(conn, a["account_id"], t0 + 90)
        # 50 of the 150 was new; 100 was the level merely still displayed
        if when == "BEFORE":
            assert counts == (0, 1, 0.0, pytest.approx(100.0))
            (g,) = rc["sections"]["historical_economic_duplicates"]
        else:
            assert counts == (1, 0, pytest.approx(100.0), 0.0)
            (g,) = rc["sections"]["economic_duplicate_suspects"]
        assert g["kinds"] == [REC.K_STILL] and g["n"] == 2
        (d,) = g["duplicate_fills"]
        assert d["book_obs_id"] == rb and d["prev_obs"] == ra
        assert d["new_liquidity"] == pytest.approx(50.0)
    finally:
        await conn.close()


@pg
async def test_a_level_that_left_the_book_in_between_is_new_liquidity():
    conn = await H.connect()
    try:
        a, slug, _g, oid = await DUP._protected_long(conn, "rc6gone",
                                                     qty=300, at=AFTER)
        ra = await H.observe(conn, slug, AFTER + 10, bids=[(0.95, 100)])
        await _old_take(conn, oid, ra, AFTER + 10, wire=0.95, displayed=100,
                        take=100.0, now=AFTER + 11)
        await H.observe(conn, slug, AFTER + 30, bids=[(0.30, 5)])
        rb = await H.observe(conn, slug, AFTER + 60, bids=[(0.95, 100)])
        await _old_take(conn, oid, rb, AFTER + 60, wire=0.95, displayed=100,
                        take=100.0, now=AFTER + 61)
        _rc, counts = await _counts(conn, a["account_id"], AFTER + 90)
        assert counts == (0, 0, 0.0, 0.0)
    finally:
        await conn.close()


@pg
async def test_the_fixed_simulator_takes_nothing_from_a_copy_of_a_read():
    """The current producer on 84a543eb's shape: the copy row offers what
    the first row offered, so the order's seen-crossing memory leaves it
    nothing; the receipt names no duplicate. (Pins existing behaviour.)"""
    conn = await H.connect()
    try:
        a, slug, _g, oid = await DUP._protected_long(conn, "rc6fixed",
                                                     qty=300, at=AFTER)
        book = dict(bids=[(0.95, 1), (0.94, 224)])
        r1 = await H.observe(conn, slug, AFTER + 10, **book)
        r2 = await H.observe(conn, slug, AFTER + 10, **book)
        await SIM.simulate_order(conn, oid, now=AFTER + 20,
                                 fee_fn=H.zero_fee)
        rows = {r["book_obs_id"] for r in await conn.fetch(
            "SELECT book_obs_id FROM paper_fills WHERE order_id=$1", oid)}
        assert r2 not in rows and rows <= {r1}
        _rc, counts = await _counts(conn, a["account_id"], AFTER + 30)
        assert counts[0] == 0 and counts[2] == 0.0
    finally:
        await conn.close()


@pg
async def test_a_truncated_read_never_claims_a_current_count(monkeypatch):
    conn = await H.connect()
    try:
        a, slug, _g, oid = await DUP._protected_long(conn, "rc6trunc",
                                                     qty=300, at=AFTER)
        book = dict(bids=[(0.95, 1), (0.94, 224)])
        r1 = await H.observe(conn, slug, AFTER + 10, **book)
        r2 = await H.observe(conn, slug, AFTER + 10, **book)
        await _old_take(conn, oid, r1, AFTER + 10, wire=0.95, displayed=1,
                        take=1.0, now=AFTER + 15)
        await _old_take(conn, oid, r2, AFTER + 10, wire=0.94, displayed=224,
                        take=224.0, now=AFTER + 15)
        monkeypatch.setattr(REC, "MAX_CANONICAL_FILLS", 1)
        monkeypatch.setattr(REC, "CANONICAL_FILLS_SQL",
                            REC.CANONICAL_FILLS_SQL.rsplit("LIMIT", 1)[0]
                            + "LIMIT 1")
        rc = await REC.receipt(conn, a["account_id"], now=AFTER + 30)
        assert rc["counts"]["duplicate_candidates_truncated"] is True
        assert rc["counts"]["economic_duplicate_suspect_groups"] is None
        assert rc["counts"]["economic_duplicate_suspect_extra_qty"] is None
        assert rc["sections"]["economic_duplicate_rule"][
            "current_complete"] is False
    finally:
        await conn.close()

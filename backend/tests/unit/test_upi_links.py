from urllib.parse import parse_qs, urlparse

import pytest

from app.money import MoneyError
from app.upi.links import is_ref_id, new_ref_id, upi_pay_link


def test_new_ref_id_format_and_uniqueness():
    refs = {new_ref_id() for _ in range(2000)}
    assert len(refs) == 2000
    assert all(is_ref_id(r) for r in refs)


@pytest.mark.parametrize("bad", [None, "", "KK", "KKABCDE", "KKABCDEFG", "XXABCDEF", "KKABCDE1",
                                 "kkabcdef"])
def test_is_ref_id_rejects(bad):
    assert not is_ref_id(bad)


def test_upi_pay_link():
    link = upi_pay_link("srilakshmikirana@okaxis", "Sri Lakshmi Kirana", 15050, "KKABCDEF",
                        "Basket 2 items & more")
    parsed = urlparse(link)
    assert parsed.scheme == "upi" and parsed.netloc == "pay"
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert q == {"pa": "srilakshmikirana@okaxis", "pn": "Sri Lakshmi Kirana", "am": "150.50",
                 "cu": "INR", "tr": "KKABCDEF", "tn": "Basket 2 items & more"}
    assert " " not in link


def test_upi_pay_link_rejects_zero():
    with pytest.raises(MoneyError):
        upi_pay_link("a@b", "x", 0, "KKABCDEF", "n")

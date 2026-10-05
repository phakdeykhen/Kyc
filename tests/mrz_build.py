"""Builds standards-correct ICAO 9303 MRZ lines for synthetic fixtures."""

from kyc.mrz.parser import check_digit


def _pad(value: str, width: int) -> str:
    return (value + "<" * width)[:width]


def _name(surname: str, given: str, width: int) -> str:
    return _pad(surname.replace(" ", "<") + "<<" + given.replace(" ", "<"), width)


def td1(code="ID", state="KHM", number="010203040", birth="900315", sex="F", expiry="291231", nationality="KHM",
        surname="SOK", given="SOPHEA", optional_1="", optional_2=""):
    first = _pad(code, 2) + state + _pad(number, 9) + check_digit(_pad(number, 9)) + _pad(optional_1, 15)
    second_head = birth + check_digit(birth) + sex + expiry + check_digit(expiry) + nationality + _pad(optional_2, 11)
    composite = check_digit(first[5:30] + second_head[0:7] + second_head[8:15] + second_head[18:29])
    return [first, second_head + composite, _name(surname, given, 30)]


def td3(state="KHM", number="N01234567", nationality="KHM", birth="900315", sex="F", expiry="300811",
        surname="SOK", given="SOPHEA", personal=""):
    first = "P<" + state + _name(surname, given, 39)
    number = _pad(number, 9)
    personal = _pad(personal, 14)
    personal_digit = check_digit(personal) if personal.strip("<") else "<"
    body = (number + check_digit(number) + nationality + birth + check_digit(birth) + sex + expiry
            + check_digit(expiry) + personal + personal_digit)
    composite = check_digit(body[0:10] + body[13:20] + body[21:43])
    return [first, body + composite]

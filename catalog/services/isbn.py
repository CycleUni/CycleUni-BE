def clean_and_validate_isbn(isbn_str):
    if not isbn_str:
        return None
    raw_isbn = str(isbn_str).replace('-', '').replace(' ', '').upper()
    if raw_isbn.isdigit() or (len(raw_isbn) == 10 and raw_isbn[:-1].isdigit() and raw_isbn[-1] in '0123456789X'):
        if len(raw_isbn) in (10, 13):
            return raw_isbn
    return None

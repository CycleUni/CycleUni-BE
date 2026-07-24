import requests

def get_by_isbn(isbn):
    response = requests.get(
        "https://www.googleapis.com/books/v1/volumes",
        params={'q': f"isbn:{isbn}"}
    )
    if response.status_code == 200:
        data = response.json()
        if data.get('totalItems', 0) > 0:
            item = data['items'][0]['volumeInfo']
            print("imageLinks:", item.get('imageLinks'))
            
get_by_isbn("9780201896831")

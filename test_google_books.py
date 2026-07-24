import requests

def search_google_books(query):
    params = {'q': query}
    try:
        response = requests.get(
            "https://www.googleapis.com/books/v1/volumes",
            params=params,
            timeout=5,
        )
        print("Status code:", response.status_code)
        if response.status_code == 200:
            print("Got results!")
            data = response.json()
            print("Total items:", data.get('totalItems'))
        else:
            print("Error response:", response.text)
    except Exception as e:
        print("Exception:", e)

search_google_books("physics")

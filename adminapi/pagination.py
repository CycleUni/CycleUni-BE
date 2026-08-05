from rest_framework.pagination import PageNumberPagination

class AdminPagination(PageNumberPagination):
    """Standard admin pagination: default 20 per page, max 100, supports page_size query param."""
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

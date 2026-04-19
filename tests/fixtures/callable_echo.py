"""Test callable module for the python-callable driver.

Provides a function that conforms to the adapter response contract:
receives a request dict, returns a response dict.
"""


def handle_request(request):
    """Echo adapter as a Python callable."""
    return {
        "raw_response": "Echo: " + request["input"],
        "parsed_response": {"echoed": request["input"]},
        "usage": {
            "input_tokens": len(request["input"].split()),
            "output_tokens": len(request["input"].split()) + 1,
        },
    }


def bad_return_type(request):
    """Returns a non-dict — should cause a driver error."""
    return "not a dict"


def raises_error(request):
    """Raises an exception — should cause a driver error."""
    raise RuntimeError("intentional error for testing")

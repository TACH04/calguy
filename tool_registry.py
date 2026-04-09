import logging

logger = logging.getLogger('tool_registry')

class ToolRegistry:
    """
    A registry for managing and dispatching LLM-accessible tools.
    """
    def __init__(self):
        self._tools = {}

    def register(self, name, description, parameters):
        """
        Decorator to register a function as a tool.
        
        Args:
            name (str): The name of the tool.
            description (str): A description of what the tool does.
            parameters (dict): JSON Schema describing the tool's parameters.
        """
        def decorator(func):
            self._tools[name] = {
                "func": func,
                "schema": {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": parameters
                    }
                }
            }
            return func
        return decorator

    def execute(self, name, arguments):
        """
        Executes a registered tool by name with the provided arguments.
        """
        if name not in self._tools:
            error_msg = f"Error: Tool '{name}' not found."
            logger.error(error_msg)
            return error_msg
        
        func = self._tools[name]["func"]
        try:
            # We assume the arguments passed by the LLM match the function signature
            return func(**arguments)
        except TypeError as e:
            # Handle cases where LLM passes extra or missing arguments
            error_msg = f"Error: Argument mismatch (TypeError) for tool '{name}': {str(e)}"
            logger.exception(error_msg)
            return error_msg
        except Exception as e:
            error_msg = f"Error executing tool '{name}' ({type(e).__name__}): {str(e)}"
            logger.exception(error_msg)
            return error_msg

    def get_ollama_tools(self):
        """
        Returns a list of tool definitions compatible with Ollama's API.
        """
        # Return tools in the order they were registered
        return [t["schema"] for t in self._tools.values()]

import unittest
from tool_registry import ToolRegistry

class TestToolRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()

    def test_registration_and_schema(self):
        @self.registry.register(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {"arg1": {"type": "string"}}, "required": ["arg1"]}
        )
        def test_tool(arg1):
            return f"Received: {arg1}"

        tools = self.registry.get_ollama_tools()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["function"]["name"], "test_tool")
        self.assertEqual(tools[0]["function"]["description"], "A test tool")
        self.assertEqual(tools[0]["function"]["parameters"]["required"], ["arg1"])

    def test_execution(self):
        @self.registry.register(
            name="add",
            description="Adds two numbers",
            parameters={"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}
        )
        def add(a, b):
            return a + b

        result = self.registry.execute("add", {"a": 10, "b": 5})
        self.assertEqual(result, 15)

    def test_missing_tool(self):
        result = self.registry.execute("non_existent", {})
        self.assertTrue(result.startswith("Error: Tool 'non_existent' not found"))

    def test_argument_mismatch(self):
        @self.registry.register(
            name="greet",
            description="Greets a person",
            parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
        )
        def greet(name):
            return f"Hello, {name}"

        # Missing required argument 'name'
        result = self.registry.execute("greet", {"wrong_arg": "value"})
        self.assertTrue("TypeError" in result or "missing" in result.lower())

    def test_tool_exception(self):
        @self.registry.register(
            name="fail",
            description="Always fails",
            parameters={"type": "object", "properties": {}, "required": []}
        )
        def fail():
            raise ValueError("Intentional failure")

        result = self.registry.execute("fail", {})
        self.assertTrue("Error executing tool 'fail'" in result)
        self.assertTrue("Intentional failure" in result)

if __name__ == "__main__":
    unittest.main()

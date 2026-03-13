"""Build and compile the orchestrator supervisor graph."""

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from graph.nodes import init_supervisor_llm, supervisor_node
from graph.state import OrchestratorState


def _should_continue(state: OrchestratorState) -> str:
    """Route to tools node if the last message has tool calls, otherwise end."""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return END


def build_orchestrator_graph(llm: BaseChatModel, tools: list):
    """Build and compile the orchestrator ReAct supervisor graph.

    Args:
        llm: Base LLM instance (will be bound to tools).
        tools: List of LangChain tools (browser_agent, chat_agent).

    Returns:
        A compiled LangGraph ``CompiledGraph`` ready for invocation.
    """
    llm_with_tools = llm.bind_tools(tools)
    init_supervisor_llm(llm_with_tools)

    builder = StateGraph(OrchestratorState)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("tools", ToolNode(tools))
    builder.set_entry_point("supervisor")
    builder.add_conditional_edges("supervisor", _should_continue)
    builder.add_edge("tools", "supervisor")

    return builder.compile()

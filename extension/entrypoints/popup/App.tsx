import '../../assets/tailwind.css';

/**
 * Popup entry point -- minimal UI that directs users to the side panel.
 * The side panel is the primary chat interface.
 */
export default function App() {
  const openSidePanel = async () => {
    const [tab] = await browser.tabs.query({
      active: true,
      currentWindow: true,
    });
    if (tab?.id) {
      await browser.sidePanel.open({ tabId: tab.id });
      window.close();
    }
  };

  return (
    <div className="w-72 bg-gray-950 text-white p-6 flex flex-col items-center gap-4">
      <div className="text-center">
        <div className="text-3xl mb-2">&#x1f916;</div>
        <h1 className="text-lg font-bold">AI Browser Assistant</h1>
        <p className="text-gray-400 text-xs mt-1">
          Open the side panel for the full chat experience
        </p>
      </div>
      <button
        onClick={openSidePanel}
        className="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2.5 px-4 rounded-lg transition-colors text-sm"
      >
        Open Side Panel
      </button>
    </div>
  );
}

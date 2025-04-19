// src/App.js
import React from 'react';
import { DirectoryProvider } from './context/DirectoryContext';
import FileExplorer from './components/FileExplorer';
import { DndProvider } from 'react-dnd';
import { HTML5Backend } from 'react-dnd-html5-backend';
import './App.css';

function App() {
  return (
    <DirectoryProvider>
      <DndProvider backend={HTML5Backend}>
        <div className="App">
          <h1>📁 File Explorer</h1>
          <FileExplorer />
        </div>
      </DndProvider>
    </DirectoryProvider>
  );
}

export default App;

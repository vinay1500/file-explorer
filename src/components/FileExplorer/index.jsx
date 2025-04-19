// src/components/FileExplorer/index.jsx
import React, { useContext, useEffect, useState } from 'react';
import { DirectoryContext } from '../../context/DirectoryContext';
import { Tree } from '@minoru/react-dnd-treeview';
import CreateNodeModal from '../Modals/CreateNodeModal';
import RenameNodeModal from '../Modals/RenameNodeModal';
import './FileExplorer.css';
import api from '../../api';

const FileExplorer = () => {
  const { tree, fetchTree, deleteNode, selectedNode, setSelectedNode } = useContext(DirectoryContext);
  const [rootId, setRootId] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [selectedId, /*setSelectedId*/] = useState(null);

  useEffect(() => {
    fetchTree();
  }, [fetchTree]);

  useEffect(() => {
    const root = tree.find(n => n.parent === null);
    if (root) setRootId(root.id);
  }, [tree]);

  const handleDrop = async (newTree, { dragSourceId, dropTargetId }) => {
    const dropTarget = newTree.find(n => n.id === dropTargetId);
    if (!dropTarget || !dropTarget.droppable) return;

    try {
      await api.patch(`/move/${dragSourceId}`, { new_parent_id: dropTargetId });
      fetchTree();
    } catch (error) {
      console.error('Error moving node:', error);
    }
  };

  const handleInlineRename = async (nodeId, newName) => {
    try {
      await api.put(`/node/${nodeId}`, { name: newName });
      fetchTree();
    } catch (error) {
      console.error('Rename failed:', error);
    }
  };
  const buildPath = (nodeId) => {
    if (!tree || !nodeId) return [];
  
    const path = [];
    let current = tree.find((n) => n.id === nodeId);
  
    while (current) {
      path.unshift(current);
      current = tree.find((n) => n.id === current.parent);
    }
  
    return path;
  };
  
  return (
    <div
  className="file-explorer"
  onClick={(e) => {
    if (e.target.classList.contains("file-explorer")) {
      setSelectedNode(null); // ✅ Deselect if clicked on blank space
    }
  }}
  style={{
    padding: "10px",
    border: "1px solid #ddd",
    borderRadius: "8px",
    minHeight: "300px", // optional: give it space to click
    backgroundColor: "#fafafa"
  }}
>
<h2
  onClick={() => setSelectedNode(null)}
  style={{ cursor: 'pointer', marginBottom: '10px' }}
  title="Click to deselect and create in root"
>
  📁 File Explorer (click to deselect)
</h2>

{selectedNode && (
  <div style={{ marginBottom: '10px', fontSize: '14px', color: '#444' }}>
    <strong>Path:</strong>{' '}
    {buildPath(selectedNode.id).map((node, index, arr) => (
      <span key={node.id}>
        <span
          onClick={() => setSelectedNode(node)}
          style={{
            cursor: 'pointer',
            color: '#007bff',
            textDecoration: 'underline',
          }}
          title={`Go to ${node.text}`}
        >
          {node.droppable ? '📁' : '🗃️'} {node.text}
        </span>
        {index < arr.length - 1 && ' / '}
      </span>
    ))}
  </div>
)}

      <Tree
        tree={tree}
        rootId={null}
        onDrop={handleDrop}
        canDrop={(tree, { dragSource, dropTargetId }) => {
          const target = tree.find(n => n.id === dropTargetId);
          return target?.droppable;
        }}
        sort={false}
        render={(node, { depth, isOpen, onToggle }) => {
          const isSelected = node.id === selectedNode?.id;
        
          const handleClick = () => {
            setSelectedNode(node);
            if (node.droppable) {
              onToggle(); // toggle open/close if folder
            }
          };
        
          return (
            <div
              onClick={handleClick}
              style={{
                marginLeft: depth * 20,
                background: isSelected ? '#e0f7fa' : 'transparent',
                padding: '4px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                borderRadius: '4px',
                cursor: 'pointer',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center' }}>
                {node.droppable && (
                  <span style={{ marginRight: 6 }}>
                    {isOpen ? '📂' : '📁'}
                  </span>
                )}
                <span
                  onDoubleClick={(e) => {
                    e.stopPropagation();
                    setEditingId(node.id);
                  }}
                  contentEditable={editingId === node.id}
                  suppressContentEditableWarning
                  onBlur={(e) => {
                    if (editingId === node.id) {
                      handleInlineRename(node.id, e.target.textContent);
                      setEditingId(null);
                    }
                  }}
                >
                  {node.text}
                </span>
              </div>
        
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  deleteNode(node.id);
                }}
                style={{ background: 'none', border: 'none', cursor: 'pointer' }}
                title="Delete"
              >
                🗑️
              </button>
            </div>
          );
        }}
        
        
        
        
        dragPreviewRender={(monitorProps) => <div>{monitorProps.item.text}</div>}
      />
      <CreateNodeModal parentId={selectedId} />
      <RenameNodeModal />
    </div>
  );
};

export default FileExplorer;

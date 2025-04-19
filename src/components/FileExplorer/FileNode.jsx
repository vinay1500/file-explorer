// src/components/FileExplorer/FileNode.jsx
import React from 'react';

const FileNode = ({ node }) => {
  return <div style={{ marginLeft: 20 }}>📄 {node.name}</div>;
};

export default FileNode;
import React, { useState } from 'react';
import FileNode from './FileNode';

const FolderNode = ({ node }) => {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div style={{ marginLeft: 20 }}>
      <div onClick={() => setIsOpen(!isOpen)}>
        📁 {node.name} {isOpen ? '▼' : '▶'}
      </div>
      {isOpen && node.children &&
        node.children.map((child) =>
          child.type === 'folder' ? (
            <FolderNode key={child.id} node={child} />
          ) : (
            <FileNode key={child.id} node={child} />
          )
        )}
    </div>
  );
};

export default FolderNode;

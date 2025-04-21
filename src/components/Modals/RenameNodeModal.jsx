import React, { useContext, useState } from 'react';
import { DirectoryContext } from '../../context/DirectoryContext';

const RenameNodeModal = () => {
  const { tree, renameNode } = useContext(DirectoryContext);
  const [show, setShow] = useState(false);
  const [selectedId, setSelectedId] = useState('');
  const [newName, setNewName] = useState('');

  const handleSubmit = () => {
    if (!selectedId || !newName) return;
    renameNode(parseInt(selectedId), newName);
    setSelectedId('');
    setNewName('');
    setShow(false);
  };

  return (
    <div>
      <button onClick={() => setShow(true)}>✏️ Rename</button>
      {show && (
        <div className="modal">
          <select
            value={selectedId}
            onChange={(e) => setSelectedId(e.target.value)}
          >
            <option value="">Select Node</option>
            {tree.map((node) => (
              <option key={node.id} value={node.id}>
                {node.text} ({node.type})
              </option>
            ))}
          </select>

          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="New name"
          />

          <button onClick={handleSubmit}>Rename</button>
          <button onClick={() => setShow(false)}>Cancel</button>
        </div>
      )}
    </div>
  );
};

export default RenameNodeModal;

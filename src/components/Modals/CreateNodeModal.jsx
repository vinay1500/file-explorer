import React, { useContext, useState } from 'react';
import { DirectoryContext } from '../../context/DirectoryContext';

const CreateNodeModal = ({ parentId }) => {
  const { createNode, selectedNode } = useContext(DirectoryContext);
  const [show, setShow] = useState(false);
  const [name, setName] = useState('');
  const [type, setType] = useState('folder');

  const handleSubmit = () => {
    const parentId = selectedNode?.droppable ? selectedNode.id : null;
    createNode({ name, type, parent_id: parentId });
    setShow(false);
    setName('');
  };

  return (
    <div>
      <button onClick={() => setShow(true)}>➕ Create</button>
      {show && (
        <div className="modal">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name"
          />
          <select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="folder">Folder</option>
            <option value="file">File</option>
          </select>
          <button onClick={handleSubmit}>Create</button>
          <button onClick={() => setShow(false)}>Cancel</button>
        </div>
      )}
    </div>
  );
};

export default CreateNodeModal;

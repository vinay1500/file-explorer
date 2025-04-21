import React, { createContext, useState, useCallback } from 'react';
import api from '../api';

export const DirectoryContext = createContext();

export const DirectoryProvider = ({ children }) => {
  const [tree, setTree] = useState([]);

  const fetchTree = useCallback(async () => {
    try {
      const res = await api.get('/tree');
      console.log('TREE:', res.data);
      setTree(res.data);
    } catch (err) {
      console.error('API fetchTree error:', err.message);
    }
  }, []);
  

  const createNode = async (data) => {
    const res = await api.post('/node', data);
    fetchTree();
    return res.data;
  };

  const renameNode = async (id, name) => {
    await api.put(`/node/${id}`, { name });
    fetchTree();
  };

  const deleteNode = async (id) => {
    await api.delete(`/node/${id}`);
    fetchTree();
  };
  const [selectedNode, setSelectedNode] = useState(null);


  return (
    <DirectoryContext.Provider value={{ tree, fetchTree, setTree, createNode, renameNode, deleteNode, selectedNode, setSelectedNode}}>
      {children}
    </DirectoryContext.Provider>
  );
};

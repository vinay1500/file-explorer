# models.py
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Node(db.Model):
    __tablename__ = 'nodes'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    type = db.Column(db.Enum('file', 'folder', name='node_type_enum'), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('nodes.id'), nullable=True)
    children = db.relationship('Node', backref=db.backref('parent', remote_side=[id]))


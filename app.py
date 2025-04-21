# backend/app.py
import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from models import db, Node

app = Flask(__name__)
CORS(app)
app.config['SQLALCHEMY_DATABASE_URI'] =  os.environ.get("DATABASE_URL")
#'mysql+pymysql://root:@localhost/file_explorer'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

@app.route('/api/tree', methods=['GET'])
def get_tree():
    nodes = Node.query.all()
    return jsonify([
        {
            "id": n.id,
            "parent": n.parent_id,
            "text": n.name,
            "type": n.type,
            "droppable": n.type == 'folder'
        }
        for n in nodes
    ])

@app.route('/')
def home():
    return "✅ Flask backend is running"


@app.route('/api/node', methods=['POST'])
def create_node():
    data = request.json
    node = Node(name=data['name'], type=data['type'], parent_id=data.get('parent_id'))
    db.session.add(node)
    db.session.commit()
    return jsonify({"id": node.id, "name": node.name}), 201

@app.route('/api/node/<int:id>', methods=['PUT'])
def rename_node(id):
    node = Node.query.get_or_404(id)
    node.name = request.json['name']
    db.session.commit()
    return jsonify({"id": node.id, "name": node.name})

@app.route('/api/node/<int:id>', methods=['DELETE'])
def delete_node(id):
    node = Node.query.get_or_404(id)
    db.session.delete(node)
    db.session.commit()
    return '', 204

@app.route('/api/move/<int:id>', methods=['PATCH'])
def move_node(id):
    node = Node.query.get_or_404(id)
    new_parent_id = request.json.get('new_parent_id')

    if new_parent_id == id:
        return jsonify({"error": "Cannot move into itself"}), 400

    node.parent_id = new_parent_id
    db.session.commit()
    return jsonify({"id": node.id, "new_parent_id": node.parent_id})


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)


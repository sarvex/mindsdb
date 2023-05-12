from flask import request
from flask_restx import Resource, abort

from sqlalchemy.exc import NoResultFound

from mindsdb.api.mysql.mysql_proxy.controllers.session_controller import SessionController

from mindsdb.api.http.namespaces.configs.projects import ns_conf


@ns_conf.route('/')
class ProjectsList(Resource):
    @ns_conf.doc('list_projects')
    def get(self):
        ''' List all projects '''
        session = SessionController()

        return [{'name': i} for i in session.datahub.get_projects_names()]


@ns_conf.route('/<project_name>')
class ProjectsGet(Resource):
    @ns_conf.doc('get_project')
    def get(self, project_name):
        '''Gets a project by name'''
        session = SessionController()

        try:
            project = session.database_controller.get_project(project_name)
        except NoResultFound:
            abort(404, f'Project name {project_name} does not exist')

        return {
            'name': project.name
        }


@ns_conf.route('/<project_name>/models')
class ModelsList(Resource):
    @ns_conf.doc('list_models')
    def get(self, project_name):
        ''' List all models '''
        session = SessionController()

        return session.model_controller.get_models(
            with_versions=True, project_name=project_name
        )


@ns_conf.route('/<project_name>/models/<model_name>/predict')
@ns_conf.param('project_name', 'Name of the project')
@ns_conf.param('predictor_name', 'Name of the model')
class ModelPredict(Resource):
    @ns_conf.doc('post_model_predict')
    def post(self, project_name, model_name):
        '''Call prediction'''

        # predictor version
        version = None
        parts = model_name.split('.')
        if len(parts) > 1 and parts[-1].isdigit():
            version = int(parts[-1])
            model_name = '.'.join(parts[:-1])

        data = request.json['data']
        params = request.json.get('params')

        session = SessionController()
        project_datanode = session.datahub.get(project_name)

        if project_datanode is None:
            abort(500, f'Project not found: {project_name}')

        predictions = project_datanode.predict(
            model_name=model_name,
            data=data,
            version=version,
            params=params,
        )

        return predictions.to_dict('records')

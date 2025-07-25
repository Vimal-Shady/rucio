# Copyright European Organization for Nuclear Research (CERN) since 2012
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from flask import Flask, jsonify, request

from rucio.common.exception import AccountNotFound, Duplicate, ScopeNotFound
from rucio.gateway.scope import add_scope, get_scopes, list_scopes
from rucio.web.rest.flaskapi.authenticated_bp import AuthenticatedBlueprint
from rucio.web.rest.flaskapi.v1.common import ErrorHandlingMethodView, check_accept_header_wrapper_flask, generate_http_error_flask, response_headers


class Scope(ErrorHandlingMethodView):

    @check_accept_header_wrapper_flask(['application/json'])
    def get(self):
        """
        ---
        summary: List Scopes
        description: "List all scopes"
        tags:
          - Scopes
        responses:
          200:
            description: "OK"
            content:
              application/json:
                schema:
                  description: "All scopes."
                  type: array
                  items:
                    description: "A scope."
                    type: string
          401:
            description: "Invalid Auth Token"
          406:
            description: "Not acceptable"
        """
        return jsonify(list_scopes(vo=request.environ['vo']))

    def post(self, account, scope):
        """
        ---
        summary: Add Scope
        description: "Adds a new scope."
        tags:
          - Scopes
        parameters:
        - name: account
          in: path
          description: "The account associated with the scope."
          schema:
            type: string
          style: simple
        - name: scope
          in: path
          description: "The name of the scope."
          schema:
            type: string
          style: simple
        responses:
          201:
            description: "OK"
            content:
              application/json:
                schema:
                  type: string
                  enum: ["Created"]
          401:
            description: "Invalid Auth Token"
          404:
            description: "Account not found"
          409:
            description: "Scope already exists"
        """
        try:
            add_scope(scope, account, issuer=request.environ['issuer'], vo=request.environ['vo'])
        except Duplicate as error:
            return generate_http_error_flask(409, error)
        except AccountNotFound as error:
            return generate_http_error_flask(404, error)

        return 'Created', 201


class AccountScopeList(ErrorHandlingMethodView):

    @check_accept_header_wrapper_flask(['application/json'])
    def get(self, account):
        """
        ---
        summary: List Account Scopes
        description: "List all scopes for an account."
        tags:
          - Scopes
        parameters:
        - name: account
          in: path
          description: "The account associated with the scope."
          schema:
            type: string
          style: simple
        responses:
          200:
            description: "OK"
            content:
              application/json:
                schema:
                  description: "All scopes for the account."
                  type: array
                  items:
                    description: "A scope for the account."
                    type: string
          401:
            description: "Invalid Auth Token"
          404:
            description: "Account not found or no scopes"
          406:
            description: "Not acceptable"
        """
        try:
            scopes = get_scopes(account, vo=request.environ['vo'])
        except AccountNotFound as error:
            return generate_http_error_flask(404, error)

        if not len(scopes):
            return generate_http_error_flask(404, ScopeNotFound.__name__, f"no scopes found for account '{account}'")

        return jsonify(scopes)


def blueprint() -> AuthenticatedBlueprint:
    bp = AuthenticatedBlueprint('scopes', __name__, url_prefix='/scopes')

    scope_view = Scope.as_view('scope')
    bp.add_url_rule('/', view_func=scope_view, methods=['get', ])
    bp.add_url_rule('/<account>/<scope>', view_func=scope_view, methods=['post', ])
    account_scope_list_view = AccountScopeList.as_view('account_scope_list')
    bp.add_url_rule('/<account>/scopes', view_func=account_scope_list_view, methods=['get', ])

    bp.after_request(response_headers)
    return bp


def make_doc():
    """ Only used for sphinx documentation """
    doc_app = Flask(__name__)
    doc_app.register_blueprint(blueprint())
    return doc_app

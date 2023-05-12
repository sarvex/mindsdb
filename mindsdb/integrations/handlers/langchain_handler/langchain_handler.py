import re
import os
from typing import Optional, Dict

import numpy as np
import pandas as pd

from langchain.llms import OpenAI
# from langchain.chat_models import ChatOpenAI  # TODO: enable chat models (including GPT4)
from langchain.agents import initialize_agent, load_tools, Tool
from langchain.prompts import PromptTemplate
from langchain.utilities import GoogleSerperAPIWrapper
from langchain.chains.conversation.memory import ConversationSummaryMemory

from mindsdb.integrations.handlers.openai_handler.openai_handler import OpenAIHandler


_DEFAULT_MODEL = 'text-davinci-003'
_DEFAULT_MAX_TOKENS = 2048  # requires more than vanilla OpenAI due to ongoing summarization and 3rd party input
_DEFAULT_AGENT_MODEL = 'zero-shot-react-description'
_DEFAULT_AGENT_TOOLS = ['python_repl', 'wikipedia']  # these require no additional arguments


class LangChainHandler(OpenAIHandler):
    """
    This is a MindsDB integration for the LangChain library, which provides a unified interface for interacting with
    various large language models (LLMs).

    Currently, this integration supports exposing OpenAI's LLMs with normal text completion support. They are then
    wrapped in a zero shot react description agent that offers a few third party tools out of the box, with support
    for additional ones if an API key is provided. Ongoing memory is also provided.

    Full tool support list:
        - wikipedia
        - python_repl
        - serper.dev search

    This integration inherits from the OpenAI engine, so it shares a lot of the requirements, features (e.g. prompt
    templating) and limitations.
    """
    name = 'langchain'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stops = []
        self.default_model = _DEFAULT_MODEL
        self.default_max_tokens = _DEFAULT_MAX_TOKENS
        self.default_agent_model = _DEFAULT_AGENT_MODEL
        self.default_agent_tools = _DEFAULT_AGENT_TOOLS

    def _get_serper_api_key(self, args, strict=True):
        if 'serper_api_key' in args:
            return args['serper_api_key']
        # 2
        connection_args = self.engine_storage.get_connection_args()
        if 'serper_api_key' in connection_args:
            return connection_args['serper_api_key']
        # 3
        api_key = os.getenv('SERPER_API_KEY')  # e.g. "OPENAI_API_KEY"
        if api_key is not None:
            return api_key

        if strict:
            raise Exception(f'Missing API key serper_api_key. Either re-create this ML_ENGINE specifying the `serper_api_key` parameter,\
                 or re-create this model and pass the API key with `USING` syntax.')  # noqa

    @staticmethod
    def create_validation(target, args=None, **kwargs):
        if 'using' not in args:
            raise Exception("LangChain engine requires a USING clause! Refer to its documentation for more details.")
        else:
            args = args['using']

        if len(set(args.keys()) & {'prompt_template'}) == 0:
            raise Exception('Please provide a `prompt_template` for this engine.')

    def predict(self, df, args=None):
        """
        Dispatch is performed depending on the underlying model type. Currently, only the default text completion
        is supported.
        """
        pred_args = args['predict_params'] if args else {}
        args = self.model_storage.json_get('args')
        df = df.reset_index(drop=True)

        if 'prompt_template' not in args and 'prompt_template' not in pred_args:
            raise Exception("This model expects a prompt template, please provide one.")

        # TODO: enable other LLM backends (AI21, Anthropic, etc.)
        if 'stops' in pred_args:
            self.stops = pred_args['stops']

        modal_dispatch = {
            'default': 'default_completion',
            'sql_agent': 'sql_agent_completion',
        }

        return getattr(self, modal_dispatch.get(args.get('mode', 'default'), 'default_completion'))(df, args, pred_args)

    def default_completion(self, df, args=None, pred_args=None):
        """
        Mostly follows the logic of the OpenAI handler, but with a few additions:
            - setup the langchain toolkit
            - setup the langchain agent (memory included)
            - setup information to be published when describing the model

        Ref link from the LangChain documentation on how to accomplish the first two items:
            - python.langchain.com/en/latest/modules/agents/agents/custom_agent.html
        """
        pred_args = pred_args if pred_args else {}

        # api argument validation
        model_name = args.get('model_name', self.default_model)
        agent_name = args.get('agent_name', self.default_agent_model)

        model_kwargs = {
            'model_name': model_name,
            'temperature': min(1.0, max(0.0, args.get('temperature', 0.0))),
            'max_tokens': pred_args.get('max_tokens', args.get('max_tokens', self.default_max_tokens)),
            'top_p': pred_args.get('top_p', None),
            'frequency_penalty': pred_args.get('frequency_penalty', None),
            'presence_penalty': pred_args.get('presence_penalty', None),
            'n': pred_args.get('n', None),
            'best_of': pred_args.get('best_of', None),
            'request_timeout': pred_args.get('request_timeout', None),
            'logit_bias': pred_args.get('logit_bias', None),
            'openai_api_key': self._get_openai_api_key(args, strict=True),
            'serper_api_key': self._get_serper_api_key(args, strict=False),
        }
        model_kwargs = {k: v for k, v in model_kwargs.items() if v is not None}  # filter out None values

        # langchain tool setup
        tools = self._setup_tools(model_kwargs, pred_args)

        # langchain agent setup
        llm = OpenAI(**model_kwargs)  # TODO: use ChatOpenAI for chat models
        memory = ConversationSummaryMemory(llm=llm)
        agent = initialize_agent(
            tools,
            llm,
            memory=memory,
            agent=agent_name,
            max_iterations=pred_args.get('max_iterations', 3),
            verbose=pred_args.get('verbose', args.get('verbose', False)),
        )

        # setup model description
        description = {
            'allowed_tools': [agent.agent.allowed_tools],   # packed as list to avoid additional rows
            'agent_type': agent_name,
            'max_iterations': agent.max_iterations,
            'memory_type': memory.__class__.__name__,
        }
        description = description | model_kwargs
        description.pop('openai_api_key', None)
        self.model_storage.json_set('description', description)

        # TODO abstract prompt templating into a common utility method, this is also used in vanilla OpenAI
        if pred_args.get('prompt_template', False):
            base_template = pred_args['prompt_template']  # override with predict-time template if available
        else:
            base_template = args['prompt_template']

        input_variables = []
        matches = list(re.finditer("{{(.*?)}}", base_template))

        for m in matches:
            input_variables.append(m[0].replace('{', '').replace('}', ''))

        empty_prompt_ids = np.where(df[input_variables].isna().all(axis=1).values)[0]

        base_template = base_template.replace('{{', '{').replace('}}', '}')
        prompts = []

        for i, row in df.iterrows():
            if i not in empty_prompt_ids:
                prompt = PromptTemplate(input_variables=input_variables, template=base_template)
                kwargs = {}
                for col in input_variables:
                    kwargs[col] = row[col] if row[col] is not None else ''  # add empty quote if data is missing
                prompts.append(prompt.format(**kwargs))

        def _completion(agent, prompts):
            # TODO: ensure that agent completion plus prompt match the maximum allowed by the user
            # TODO: use async API if possible for parallelized completion
            completion = [agent.run(prompt) for prompt in prompts]
            return list(completion)

        completion = _completion(agent, prompts)

        # add null completion for empty prompts
        for i in sorted(empty_prompt_ids):
            completion.insert(i, None)

        pred_df = pd.DataFrame(completion, columns=[args['target']])

        return pred_df

    def _setup_tools(self, model_kwargs, pred_args):
        toolkit = pred_args.get('tools', self.default_agent_tools)
        tools = load_tools(toolkit)
        if model_kwargs.get('serper_api_key', False):
            search = GoogleSerperAPIWrapper(serper_api_key=model_kwargs.pop('serper_api_key'))
            tools.append(Tool(
                name="Intermediate Answer (serper.dev)",
                func=search.run,
                description="useful for when you need to ask with search"
            ))
        return tools

    def describe(self, attribute: Optional[str] = None) -> pd.DataFrame:
        info = self.model_storage.json_get('description')

        if attribute == 'info':
            if info is None:
                # we do this due to the huge amount of params that can be changed
                #  at prediction time to customize behavior.
                # for them, we report the last observed value
                raise Exception('This model needs to be used before it can be described.')

            return pd.DataFrame(info)
        else:
            tables = ['info']
            return pd.DataFrame(tables, columns=['tables'])

    def finetune(self, df: Optional[pd.DataFrame] = None, args: Optional[Dict] = None) -> None:
        raise NotImplementedError('Fine-tuning is not supported for LangChain models')

    def sql_agent_completion(self, df, args=None):
        """This completion will be used to answer based on information passed by any MindsDB DB or API engine."""
        # TODO: figure out best way to pass DB/API dataframes to LLM handlers
        raise NotImplementedError()

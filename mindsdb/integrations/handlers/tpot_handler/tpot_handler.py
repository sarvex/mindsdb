from typing import Optional
import dill
import pandas as pd
from mindsdb.integrations.libs.base import BaseMLEngine
from typing import Dict, Optional
from type_infer.infer import infer_types
from tpot import TPOTClassifier, TPOTRegressor


class TPOTHandler(BaseMLEngine):
    name = "TPOT"
    def create(self, target: str, df: Optional[pd.DataFrame] = None, args: Optional[Dict] = None) -> None:
        if args is None:
            args = {}
        
        target_dtype=infer_types(df,0).to_dict()["dtypes"][target]


        if target_dtype in ['binary','categorical','tags']:
            model = TPOTClassifier(generations=args.get('generations', 10),
                                       population_size=args.get('population_size', 100),
                                       verbosity=0,
                                       max_time_mins=args.get('max_time_mins', None),
                                       n_jobs=args.get('n_jobs', -1))
            

        elif target_dtype in ['integer','float','quantity'] :
            model = TPOTRegressor(generations=args.get('generations', 10),
                                      population_size=args.get('population_size', 100),
                                      verbosity=0,
                                      max_time_mins=args.get('max_time_mins', None),
                                      n_jobs=args.get('n_jobs', -1))
        

        if df is not None:
            model.fit(df.drop(columns=[target]), df[target])
            self.model_storage.json_set('args', args)
            self.model_storage.file_set('model', dill.dumps(model.fitted_pipeline_))
        else :
            raise Exception(
                "Data is empty!!"
            )
        

    def predict(self, df: pd.DataFrame, args: Optional[Dict] = None) -> pd.DataFrame:

        model=dill.loads(self.model_storage.file_get("model"))
        target=self.model_storage.json_get('args').get("target")

        return pd.DataFrame(model.predict(df),columns=[target])

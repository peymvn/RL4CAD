"""
This script evaluates the behavior policy of one group on a different group's data (e.g., behavior policy of Calgary physicians on Edmonton data or vice versa).
"""

import pandas as pd
import os
import numpy as np
import pickle
from datetime import datetime
from sklearn import preprocessing
import argparse
import transfer_constants as trc
from rl_utils import get_episodes, get_d3rlpy_dataset, PolicyResolver, weighted_importance_sampling_with_bootstrap, reward_func_mace_survival_repvasc, ClusteringBasedInference


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Evaluate the behavior policy of one group on the other group data')
    parser.add_argument('--stratify_on', type=str, default='hospital', help='Stratify on which feature?')
    args = parser.parse_args()
    stratify_on = args.stratify_on
    groups = trc.stratification_consts[stratify_on]['groups']

    results_dir = os.path.join(trc.EXPERIMENTS_RESULTS, stratify_on)
    os.makedirs(results_dir, exist_ok=True)

    rewards_list = trc.rewards_list
    reward_function = reward_func_mace_survival_repvasc

    main_data_folder = trc.stratification_consts[stratify_on]['processed_data']
    main_models_folder = trc.stratification_consts[stratify_on]['models']
    behavior_experiment_type = trc.experiment_type_behavior_policy

    # encode the actions
    treatments = trc.treatments
    action_encoder = preprocessing.LabelEncoder()
    action_encoder.fit(np.array(treatments).reshape(-1, 1))
    actions_dict = dict(zip(action_encoder.classes_, action_encoder.transform(action_encoder.classes_)))
    print("Actions dictionary:", actions_dict)

    data_folder_dict = {}
    models_folder_dict = {}
    behavior_policy_dict = {}
    behavior_clusters_dict = {}
    episodes_dict = {}
    for group in groups:

        # load the behavior policy
        data_folder_dict[group] = os.path.join(main_data_folder, group)
        models_folder_dict[group] = os.path.join(main_models_folder, group)
        behavior_policy_path = os.path.join(models_folder_dict[group], f"behavior_policy_{behavior_experiment_type}.pkl")
        behavior_policy_data = pickle.load(open(behavior_policy_path, 'rb'))
        behavior_n_clusters = behavior_policy_data['n_clusters']
        behavior_policy_cluster_model_path = os.path.join(models_folder_dict[group], f"{behavior_experiment_type}_models", f"{behavior_experiment_type}_{behavior_n_clusters}.pkl")
        behavior_policy_cluster_model = pickle.load(open(behavior_policy_cluster_model_path, 'rb'))
        behavior_policy_dict[group] = ClusteringBasedInference(behavior_policy_cluster_model,
                                                               behavior_policy_data['n_clusters'],
                                                               behavior_policy_data['behavior_policy'])
        
        # load the test data
        all_caths_test = pd.read_csv(os.path.join(data_folder_dict[group], 'test', 'all_caths.csv'))
        all_caths_test['SubsequentTreatment'].fillna('Medical Therapy', inplace=True)
        all_caths_test_imputed = pd.read_csv(os.path.join(data_folder_dict[group], 'test', 'all_caths_test_imputed.csv'))

        # drop feature containing the groups' information
        group_features_to_drop = trc.stratification_consts[stratify_on]['features_to_drop']
        all_caths_test.drop(columns=group_features_to_drop, inplace=True)
        all_caths_test_imputed.drop(columns=group_features_to_drop, inplace=True)

        test_episodes = get_episodes(all_caths_test, all_caths_test_imputed, action_encoder, rewards_list, reward_function)

        # put behavior policy in the test episodes
        for episode in test_episodes:
            for transition in episode:
                transition.prediction_probs['behavior_policy'] = behavior_policy_dict[group].get_policy(transition.state.reshape(1, -1))[0,:].tolist()

        episodes_dict[group] = test_episodes

    # evaluate the behavior policy of one group on the other group's data
    results_df = pd.DataFrame(columns=['evaluation_on'] + [f"Pi_{group}_wis" for group in groups] + [f"Pi_{group}_ci" for group in groups] + [f"Pi_{group}_greedy_wis" for group in groups] + [f"Pi_{group}_greedy_ci" for group in groups])
    result_filename = os.path.join(results_dir, f"{stratify_on}_evaluate_on_opposing_behavior_policy_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv")
    for group_of_data in groups:
        test_episodes = episodes_dict[group_of_data]
        policy_eval_results = {}
        policy_eval_results['evaluation_on'] = group_of_data
        behavior_pi_B = PolicyResolver('behavior_policy',  list(actions_dict.values()))
        greedy_behavior_pi_B = PolicyResolver('behavior_policy',  list(actions_dict.values()), greedy=True)

        wis, ci = weighted_importance_sampling_with_bootstrap(test_episodes, 0.99, behavior_pi_B, behavior_pi_B, num_bootstrap_samples=1000, N=1000)
        policy_eval_results[f"Pi_{group_of_data}_wis"] = wis
        policy_eval_results[f"Pi_{group_of_data}_ci"] = ci

        wis, ci = weighted_importance_sampling_with_bootstrap(test_episodes, 0.99, greedy_behavior_pi_B, behavior_pi_B, num_bootstrap_samples=1000, N=1000)
        policy_eval_results[f"Pi_{group_of_data}_greedy_wis"] = wis
        policy_eval_results[f"Pi_{group_of_data}_greedy_ci"] = ci

        for group_of_policy in groups:
            if group_of_policy == group_of_data:
                continue
            print(f"Evaluating the behavior policy of {group_of_policy} on the data of {group_of_data}")
            opposing_behavior_policy = behavior_policy_dict[group_of_policy]

            # put opposing behavior policy in the test episodes
            for episode in test_episodes:
                for transition in episode:
                    transition.prediction_probs['opposing_behavior_policy'] = opposing_behavior_policy.get_policy(transition.state.reshape(1, -1))[0,:].tolist()

            # evaluate the behavior policy
            opposing_pi_B = PolicyResolver('opposing_behavior_policy',  list(actions_dict.values()))
            greedy_opp_pi_B = PolicyResolver('opposing_behavior_policy',  list(actions_dict.values()), greedy=True)
            
            wis, ci = weighted_importance_sampling_with_bootstrap(test_episodes, 0.99, opposing_pi_B, behavior_pi_B, num_bootstrap_samples=1000, N=1000)
            policy_eval_results[f"Pi_{group_of_policy}_wis"] = wis
            policy_eval_results[f"Pi_{group_of_policy}_ci"] = ci

            wis, ci = weighted_importance_sampling_with_bootstrap(test_episodes, 0.99, greedy_opp_pi_B, behavior_pi_B, num_bootstrap_samples=1000, N=1000)
            policy_eval_results[f"Pi_{group_of_policy}_greedy_wis"] = wis
            policy_eval_results[f"Pi_{group_of_policy}_greedy_ci"] = ci

            print(policy_eval_results)

        # save the results
        results_df = pd.concat([results_df, pd.DataFrame([policy_eval_results])])
        results_df.to_csv(os.path.join(results_dir, f"evaluate_on_opposing_policy_temp.csv"), index=False)

    results_df.to_csv(result_filename, index=False)
    os.remove(os.path.join(results_dir, f"evaluate_on_opposing_policy_temp.csv"))
    print("Done!")






            
        


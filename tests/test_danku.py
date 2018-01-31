from dutils.dataset import SampleHalfDividedDataset
from dutils.neural_network import NeuralNetwork
import dutils.debug as dbg
from secrets import randbelow

def scale_packed_data(data, scale):
    # Scale data and convert it to an integer
    return list(map(lambda x: int(x*scale), data))

def test_single_solver_finalized_contract(web3, chain):
    _hashed_data_groups = []
    accuracy_criteria = 5000 # 50.00%

    w_scale = 1000 # Scale up weights by 1000x
    b_scale = 1000 # Scale up biases by 1000x

    danku, _ = chain.provider.get_or_deploy_contract('Danku')

    offer_account = web3.eth.accounts[1]
    solver_account = web3.eth.accounts[2]

    # Fund contract
    web3.eth.sendTransaction({
		'from': offer_account,
		'to': danku.address,
		'value': web3.toWei(1, "ether")
	})

    # Check that offerer was deducted
    bal = web3.eth.getBalance(offer_account)
    # Deduct reward amount (1 ETH) and gas cost (21040 wei)
    assert bal == 999998999999999999978960

    wallet_amount = 1000000000000000000000000 # minus the reward amount

    scd = SampleHalfDividedDataset(training_percentage=0.8)
    scd.generate_nonce()
    scd.sha_all_data_groups()

    dbg.dprint("All data groups: " + str(scd.data))
    dbg.dprint("All nonces: " + str(scd.nonce))

    # Initialization step 1
    dbg.dprint("Hashed data groups: " + str(scd.hashed_data_group))
    dbg.dprint("Hashed Hex data groups: " +
        str(list(map(lambda x: "0x" + x.hex(), scd.hashed_data_group))))

    # Keep track of all block numbers, so we can send them in time
    # Start at a random block between 0-1000
    chain.wait.for_block(randbelow(1000))
    dbg.dprint("Starting block: " + str(web3.eth.blockNumber))
    init1_tx = danku.transact().init1(scd.hashed_data_group, accuracy_criteria,
        offer_account)
    chain.wait.for_receipt(init1_tx)
    init1_block_number = web3.eth.blockNumber
    dbg.dprint("Init1 block: " + str(init1_block_number))

    submission_t = danku.call().submission_stage_block_size() # get submission timeframe
    evaluation_t = danku.call().evaluation_stage_block_size() # get evaluation timeframe
    test_reveal_t = danku.call().reveal_test_data_groups_block_size() # get revealing testing dataset timeframe

    # Initialization step 2
    # Get data group indexes
    chain.wait.for_block(init1_block_number + 1)
    dgi = []
    init2_block_number = web3.eth.blockNumber
    dbg.dprint("Init2 block: " + str(init2_block_number))

    for i in range(scd.num_data_groups):
        dgi.append(i)

    dbg.dprint("Data group indexes: " + str(dgi))

    init2_tx = danku.transact().init2()
    chain.wait.for_receipt(init2_tx)

    # Can only access one element of a public array at a time
    training_partition = list(map(lambda x: danku.call().training_partition(x),\
        range(scd.num_train_data_groups)))
    testing_partition = list(map(lambda x: danku.call().testing_partition(x),\
        range(scd.num_test_data_groups)))
    # get partitions
    dbg.dprint("Training partition: " + str(training_partition))
    dbg.dprint("Testing partition: " + str(testing_partition))

    scd.partition_dataset(training_partition, testing_partition)
    # Initialization step 3
    # Time to reveal the training dataset
    training_nonces = []
    training_data = []
    for i in training_partition:
        training_nonces.append(scd.nonce[i])
    # Pack data into a 1-dimension array
    # Since the data array is too large, we're going to send them in single data group chunks
    train_data = scd.pack_data(scd.train_data)
    test_data = scd.pack_data(scd.test_data)
    init3_tx = []
    for i in range(len(training_partition)):
        start = i*scd.dps*scd.partition_size
        end = start + scd.dps*scd.partition_size
        dbg.dprint("(" + str(training_partition[i]) + ") Train data,nonce: " + str(train_data[start:end]) + "," + str(scd.train_nonce[i]))
        init3_tx.append(danku.transact().init3(train_data[start:end], scd.train_nonce[i]))
        chain.wait.for_receipt(init3_tx[i])

    init3_block_number = web3.eth.blockNumber
    dbg.dprint("Init3 block: " + str(init3_block_number))

    # Get the training data from the contract
    contract_train_data_length = danku.call().get_train_data_length()
    contract_train_data = []
    for i in range(contract_train_data_length):
        for j in range(scd.dps):
            contract_train_data.append(danku.call().train_data(i,j))
    contract_train_data = scd.unpack_data(contract_train_data)
    dbg.dprint("Contract training data: " + str(contract_train_data))

    il_nn = 2
    hl_nn = []
    ol_nn = 2
    # Train a neural network with contract data
    nn = NeuralNetwork(il_nn, hl_nn, ol_nn)
    contract_train_data = nn.binary_2_one_hot(contract_train_data)
    nn.load_train_data(contract_train_data)
    nn.init_network()
    nn.train()
    trained_weights = nn.weights
    trained_biases = nn.bias

    dbg.dprint("Trained weights: " + str(trained_weights))
    dbg.dprint("Trained biases: " + str(trained_biases))

    packed_trained_weights = nn.pack_weights(trained_weights)
    dbg.dprint("Packed weights: " + str(packed_trained_weights))

    packed_trained_biases = nn.pack_biases(trained_biases)
    dbg.dprint("Packed biases: " + str(packed_trained_biases))

    int_packed_trained_weights = scale_packed_data(packed_trained_weights,\
        w_scale)
    dbg.dprint("Packed integer weights: " + str(int_packed_trained_weights))

    int_packed_trained_biases = scale_packed_data(packed_trained_biases,\
        b_scale)
    dbg.dprint("Packed integer biases: " + str(int_packed_trained_biases))

    dbg.dprint("Solver address: " + str(solver_account))

    # Submit the solution to the contract
    submit_tx = danku.transact().submit_model(solver_account, il_nn, ol_nn, hl_nn,\
        int_packed_trained_weights, int_packed_trained_biases)
    chain.wait.for_receipt(submit_tx)

    # Get submission index ID
    submission_id = danku.call().get_submission_id(solver_account, il_nn,\
        ol_nn, hl_nn, int_packed_trained_weights, int_packed_trained_biases)
    dbg.dprint("Submission ID: " + str(submission_id))

    # Wait until the submission period ends
    chain.wait.for_block(init3_block_number + submission_t)

    # Reveal the testing dataset after the submission period ends
    reveal_tx = []
    for i in range(len(testing_partition)):
        start = i*scd.dps*scd.partition_size
        end = start + scd.dps*scd.partition_size
        dbg.dprint("(" + str(testing_partition[i]) + ") Test data,nonce: " + str(test_data[start:end]) + "," + str(scd.test_nonce[i]))
        reveal_tx.append(danku.transact().reveal_test_data(test_data[start:end], scd.test_nonce[i]))
        chain.wait.for_receipt(reveal_tx[i])

    # Wait until the test reveal period ends
    chain.wait.for_block(init3_block_number + submission_t + test_reveal_t)

    # Evaluate the submitted solution
    eval_tx = danku.transact().evaluate_model(submission_id)

    # Wait until the evaluation period ends
    chain.wait.for_block(init3_block_number + submission_t + test_reveal_t + evaluation_t)

    bal2 = web3.eth.getBalance(offer_account)

    # Finalize the contract
    final_tx = danku.transact().finalize_contract()

    contract_finalized = danku.call().contract_terminated()

    dbg.dprint("Contract finalized: " + str(contract_finalized))

    assert contract_finalized == True

    # Get best submission accuracy & ID
    best_submission_accuracy = danku.call().best_submission_accuracy()
    best_submission_index = danku.call().best_submission_index()

    dbg.dprint("Best submission ID: " + str(best_submission_index))
    dbg.dprint("Best submission accuracy: " + str(best_submission_accuracy))

    l_nn = [il_nn] + hl_nn + [ol_nn]
    input_layer = train_data[:2]
    hidden_layers = [0] * sum(hl_nn)
    output_layer = [0] * ol_nn
    weights = int_packed_trained_weights
    biases = int_packed_trained_biases
    # Test forward
    fwd_pass2 = danku.call().forward_pass2(l_nn, input_layer, hidden_layers, output_layer, weights, biases)

    dbg.dprint("Test input: " + str(train_data[:2]))
    dbg.dprint("Expected output: " + str(train_data[2]))
    dbg.dprint("local nn prediction: " + str(nn.predict([train_data[:2]])))

    dbg.dprint("forward_pass2: " + str(fwd_pass2))

    bal = web3.eth.getBalance(solver_account)

    # Verify that the solver account received the reward amount
    assert bal == 1000001000000000000000000

    bal = web3.eth.getBalance(offer_account)

    # Verify the offer account balance
    assert bal == 999998999999999999978960

def test_single_solver_refunded_contract(web3, chain):
    _hashed_data_groups = []
    accuracy_criteria = 9950 # 99.50%

    w_scale = 1000 # Scale up weights by 1000x
    b_scale = 1000 # Scale up biases by 1000x

    dbg.dprint("Start amount bal[0]: " + str(web3.eth.getBalance(web3.eth.accounts[0])))

    danku, _ = chain.provider.get_or_deploy_contract('Danku')

    offer_account = web3.eth.accounts[1]
    solver_account = web3.eth.accounts[2]

    # Fund contract
    web3.eth.sendTransaction({
		'from': offer_account,
		'to': danku.address,
		'value': web3.toWei(1, "ether")
	})

    # Check that offerer was deducted
    bal = web3.eth.getBalance(offer_account)
    # Deduct reward amount (1 ETH) and gas cost (21040 wei)
    assert bal == 999998999999999999978960

    wallet_amount = 1000000000000000000000000 # minus the reward amount

    scd = SampleHalfDividedDataset(training_percentage=0.8)
    scd.generate_nonce()
    scd.sha_all_data_groups()

    dbg.dprint("All data groups: " + str(scd.data))
    dbg.dprint("All nonces: " + str(scd.nonce))

    # Initialization step 1
    dbg.dprint("Hashed data groups: " + str(scd.hashed_data_group))
    dbg.dprint("Hashed Hex data groups: " +
        str(list(map(lambda x: "0x" + x.hex(), scd.hashed_data_group))))

    # Keep track of all block numbers, so we can send them in time
    # Start at a random block between 0-1000
    chain.wait.for_block(randbelow(1000))
    dbg.dprint("Starting block: " + str(web3.eth.blockNumber))
    init1_tx = danku.transact().init1(scd.hashed_data_group, accuracy_criteria,
        offer_account)
    chain.wait.for_receipt(init1_tx)
    init1_block_number = web3.eth.blockNumber
    dbg.dprint("Init1 block: " + str(init1_block_number))

    submission_t = danku.call().submission_stage_block_size() # get submission timeframe
    evaluation_t = danku.call().evaluation_stage_block_size() # get evaluation timeframe
    test_reveal_t = danku.call().reveal_test_data_groups_block_size() # get revealing testing dataset timeframe

    # Initialization step 2
    # Get data group indexes
    chain.wait.for_block(init1_block_number + 1)
    dgi = []
    init2_block_number = web3.eth.blockNumber
    dbg.dprint("Init2 block: " + str(init2_block_number))

    for i in range(scd.num_data_groups):
        dgi.append(i)

    dbg.dprint("Data group indexes: " + str(dgi))

    init2_tx = danku.transact().init2()
    chain.wait.for_receipt(init2_tx)

    # Can only access one element of a public array at a time
    training_partition = list(map(lambda x: danku.call().training_partition(x),\
        range(scd.num_train_data_groups)))
    testing_partition = list(map(lambda x: danku.call().testing_partition(x),\
        range(scd.num_test_data_groups)))
    # get partitions
    dbg.dprint("Training partition: " + str(training_partition))
    dbg.dprint("Testing partition: " + str(testing_partition))

    scd.partition_dataset(training_partition, testing_partition)
    # Initialization step 3
    # Time to reveal the training dataset
    training_nonces = []
    training_data = []
    for i in training_partition:
        training_nonces.append(scd.nonce[i])
    # Pack data into a 1-dimension array
    # Since the data array is too large, we're going to send them in single data group chunks
    train_data = scd.pack_data(scd.train_data)
    test_data = scd.pack_data(scd.test_data)
    init3_tx = []
    for i in range(len(training_partition)):
        start = i*scd.dps*scd.partition_size
        end = start + scd.dps*scd.partition_size
        dbg.dprint("(" + str(training_partition[i]) + ") Train data,nonce: " + str(train_data[start:end]) + "," + str(scd.train_nonce[i]))
        init3_tx.append(danku.transact().init3(train_data[start:end], scd.train_nonce[i]))
        chain.wait.for_receipt(init3_tx[i])

    init3_block_number = web3.eth.blockNumber
    dbg.dprint("Init3 block: " + str(init3_block_number))

    # Get the training data from the contract
    contract_train_data_length = danku.call().get_train_data_length()
    contract_train_data = []
    for i in range(contract_train_data_length):
        for j in range(scd.dps):
            contract_train_data.append(danku.call().train_data(i,j))
    contract_train_data = scd.unpack_data(contract_train_data)
    dbg.dprint("Contract training data: " + str(contract_train_data))

    il_nn = 2
    hl_nn = []
    ol_nn = 2
    # Train a neural network with contract data
    nn = NeuralNetwork(il_nn, hl_nn, ol_nn)
    contract_train_data = nn.binary_2_one_hot(contract_train_data)
    nn.load_train_data(contract_train_data)
    nn.init_network()
    nn.train()
    trained_weights = nn.weights
    trained_biases = nn.bias

    dbg.dprint("Trained weights: " + str(trained_weights))
    dbg.dprint("Trained biases: " + str(trained_biases))

    packed_trained_weights = nn.pack_weights(trained_weights)
    dbg.dprint("Packed weights: " + str(packed_trained_weights))

    packed_trained_biases = nn.pack_biases(trained_biases)
    dbg.dprint("Packed biases: " + str(packed_trained_biases))

    int_packed_trained_weights = scale_packed_data(packed_trained_weights,\
        w_scale)
    dbg.dprint("Packed integer weights: " + str(int_packed_trained_weights))

    int_packed_trained_biases = scale_packed_data(packed_trained_biases,\
        b_scale)
    dbg.dprint("Packed integer biases: " + str(int_packed_trained_biases))

    dbg.dprint("Solver address: " + str(solver_account))

    # Submit the solution to the contract
    submit_tx = danku.transact().submit_model(solver_account, il_nn, ol_nn, hl_nn,\
        int_packed_trained_weights, int_packed_trained_biases)
    chain.wait.for_receipt(submit_tx)

    # Get submission index ID
    submission_id = danku.call().get_submission_id(solver_account, il_nn,\
        ol_nn, hl_nn, int_packed_trained_weights, int_packed_trained_biases)
    dbg.dprint("Submission ID: " + str(submission_id))

    # Wait until the submission period ends
    chain.wait.for_block(init3_block_number + submission_t)

    # Reveal the testing dataset after the submission period ends
    reveal_tx = []
    for i in range(len(testing_partition)):
        start = i*scd.dps*scd.partition_size
        end = start + scd.dps*scd.partition_size
        dbg.dprint("(" + str(testing_partition[i]) + ") Test data,nonce: " + str(test_data[start:end]) + "," + str(scd.test_nonce[i]))
        reveal_tx.append(danku.transact().reveal_test_data(test_data[start:end], scd.test_nonce[i]))
        chain.wait.for_receipt(reveal_tx[i])

    # Wait until the test reveal period ends
    chain.wait.for_block(init3_block_number + submission_t + test_reveal_t)

    # Evaluate the submitted solution
    eval_tx = danku.transact().evaluate_model(submission_id)

    # Wait until the evaluation period ends
    chain.wait.for_block(init3_block_number + submission_t + test_reveal_t + evaluation_t)

    bal2 = web3.eth.getBalance(offer_account)

    # Finalize the contract
    final_tx = danku.transact().finalize_contract()

    contract_finalized = danku.call().contract_terminated()

    dbg.dprint("Contract finalized: " + str(contract_finalized))

    assert contract_finalized == True

    # Get best submission accuracy & ID
    best_submission_accuracy = danku.call().best_submission_accuracy()
    best_submission_index = danku.call().best_submission_index()

    dbg.dprint("Best submission ID: " + str(best_submission_index))
    dbg.dprint("Best submission accuracy: " + str(best_submission_accuracy))

    l_nn = [il_nn] + hl_nn + [ol_nn]
    input_layer = train_data[:2]
    hidden_layers = [0] * sum(hl_nn)
    output_layer = [0] * ol_nn
    weights = int_packed_trained_weights
    biases = int_packed_trained_biases
    # Test forward
    fwd_pass2 = danku.call().forward_pass2(l_nn, input_layer, hidden_layers, output_layer, weights, biases)

    dbg.dprint("Test input: " + str(train_data[:2]))
    dbg.dprint("Expected output: " + str(train_data[2]))
    dbg.dprint("local nn prediction: " + str(nn.predict([train_data[:2]])))

    dbg.dprint("forward_pass2: " + str(fwd_pass2))

    bal = web3.eth.getBalance(solver_account)

    # Verify that the solver account didn't receive the reward amount
    assert bal == 1000000000000000000000000

    bal = web3.eth.getBalance(offer_account)

    # Verify the offer account got refunded the reward amount
    assert bal == 999999999999999999978960

'''
def test_python_solidity_hashing_compatability():
    # Make sure Python and solidity hashes the data groups in the same manner
    assert(False)

def test_dataset_partitioning():
    assert(False)

def test_train_single_layer_neural_network():
    assert(False)

def test_train_multi_layer_neural_network():
    assert(False)

def test_danku_model_submission():
    assert(False)

def test_danku_reveal_test_data():
    assert(False)

def test_danku_dont_reveal_test_data():
    assert(False)

def test_danku_evaluate_model():
    assert(False)

def test_danku_cancel_contract():
    assert(False)

def test_danku_finalize_contract():
    assert(False)

def test_danku_model_accuracy():
    assert(False)
'''

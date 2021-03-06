import time

import tensorflow as tf
import tensorflow.contrib.slim as slim
import cifarnet
import numpy as np
import os

os.environ["CUDA_VISIBLE_DEVICES"] = "3"


_BATCH_SIZE = 50
lr = 0.0001

X = tf.placeholder(tf.float32, [_BATCH_SIZE, 32, 32, 3])
X_adv = tf.placeholder(tf.float32, [_BATCH_SIZE, 32, 32, 3])
Y = tf.placeholder(tf.float32, [_BATCH_SIZE, 10])
keep_prop = tf.placeholder("float")


def bulid_Net(image, reuse=tf.AUTO_REUSE):
    image = tf.reshape(image, [-1, 32, 32, 3])
    with tf.variable_scope(name_or_scope='CifarNet', reuse=reuse):
        arg_scope = cifarnet.cifarnet_arg_scope()
        with slim.arg_scope(arg_scope):
            logits, end_point = cifarnet.cifarnet(image, 10, is_training=True, dropout_keep_prob=keep_prop)
            probs = tf.nn.softmax(logits)  # probabilities
    return logits, probs, end_point


def step_target_class_adversarial_images(x, eps, one_hot_target_class):
    logits, probs, end_points = bulid_Net(x)
    cross_entropy = tf.losses.softmax_cross_entropy(one_hot_target_class,
                                                    logits,
                                                    label_smoothing=0.1,
                                                    weights=1.0)
    x_adv = x - eps * tf.sign(tf.gradients(cross_entropy, x)[0])
    x_adv = tf.clip_by_value(x_adv, -1.0, 1.0)
    return tf.stop_gradient(x_adv)


def stepll_adversarial_images(x, eps):
    logits, probs, end_points = bulid_Net(x)
    least_likely_class = tf.argmin(logits, 1)
    one_hot_ll_class = tf.one_hot(least_likely_class, 10)
    return step_target_class_adversarial_images(x, eps, one_hot_ll_class)


def stepllnoise_adversarial_images(x, eps):
    logits, probs, end_points = bulid_Net(x)
    least_likely_class = tf.argmin(logits, 1)
    one_hot_ll_class = tf.one_hot(least_likely_class, 10)
    x_noise = x + eps / 2 * tf.sign(tf.random_normal(x.shape))
    return step_target_class_adversarial_images(x_noise, eps / 2, one_hot_ll_class)


def grad_cam(end_point, pre_calss_one_hot, layer_name='pool2'):
    conv_layer = end_point[layer_name]
    signal = tf.multiply(end_point['Logits'], pre_calss_one_hot)
    loss = tf.reduce_mean(signal, 1)
    grads = tf.gradients(loss, conv_layer)[0]
    norm_grads = tf.div(grads, tf.sqrt(tf.reduce_mean(tf.square(grads))) + tf.constant(1e-5))
    weights = tf.reduce_mean(norm_grads, axis=(1, 2))
    weights = tf.expand_dims(weights, 1)
    weights = tf.expand_dims(weights, 1)
    weights = tf.tile(weights, [1, 8, 8, 1])
    pre_cam = tf.multiply(weights, conv_layer)
    cam = tf.reduce_sum(pre_cam, 3)
    cam = tf.expand_dims(cam, 3)
    """"""
    cam = tf.reshape(cam, [-1, 64])
    cam = tf.nn.softmax(cam)
    cam = tf.reshape(cam, [-1, 8, 8, 1])
    # cam = tf.nn.relu(cam)
    resize_cam = tf.image.resize_images(cam, [32, 32])
    # resize_cam = resize_cam / tf.reduce_max(resize_cam)
    return resize_cam, cam


def mask_image(img, grad_cam):
    img = tf.reshape(img, [-1, 32, 32, 3])
    cam_clip = tf.sign(tf.nn.relu(grad_cam - tf.reduce_mean(grad_cam)))
    reverse_cam = tf.abs(cam_clip - 1)
    return reverse_cam * img


def save():
    saver = tf.train.Saver()
    saver.save(sess, "model_1/model.ckpt")

adv_sample_get_op = stepll_adversarial_images(X, tf.random_uniform([1], 0, 0.3))
fixed_adv_sample_get_op = stepll_adversarial_images(X, 0.15)
NOISE_adv_sample_get_op = stepllnoise_adversarial_images(X, 0.15)

rar_logits, rar_probs, rar_end_point = bulid_Net(X)
adv_logits, adv_probs, adv_end_point = bulid_Net(X_adv)

with tf.name_scope("classifition_loss"):
    loss_1 = slim.losses.softmax_cross_entropy(rar_logits, Y)
    loss_2 = slim.losses.softmax_cross_entropy(adv_logits, Y)
    regularization = tf.add_n(slim.losses.get_regularization_losses())
    classification_loss = loss_1 + loss_2 + regularization

rar_grad_cam, rar_rarcam = grad_cam(rar_end_point, Y)
adv_grad_cam, adv_rarcam = grad_cam(adv_end_point, Y)

with tf.name_scope("grad_cam_loss"):
    # grad_cam_loss = tf.reduce_sum(tf.abs(rar_grad_cam - adv_grad_cam))
    grad_cam_loss = tf.reduce_sum(tf.square(rar_grad_cam - adv_grad_cam))

mask_X = mask_image(X, rar_grad_cam)
mask_X_adv = mask_image(X_adv, adv_grad_cam)

Mrar_logits, Mrar_probs, Mrar_end_point = bulid_Net(mask_X)
Madv_logits, Madv_probs, Madv_end_point = bulid_Net(mask_X_adv)

with tf.name_scope("attation_loss"):
    a_1 = -tf.reduce_mean(tf.square(rar_probs - Mrar_probs))
    a_2 = -tf.reduce_mean(tf.square(adv_probs - Madv_probs))
    # a_1 = -slim.losses.softmax_cross_entropy(Mrar_logits, Y)
    # a_2 = -slim.losses.softmax_cross_entropy(Madv_logits, Y)
    attention_loss = a_1 + a_2

with tf.name_scope("total_loss"):
    total_loss = 8 * classification_loss + 4 * attention_loss + 1 * grad_cam_loss

with tf.name_scope("loss"):
    tf.summary.scalar('total_loss', total_loss)
    tf.summary.scalar('classification_loss', classification_loss)
    tf.summary.scalar('attention_loss', attention_loss)
    tf.summary.scalar('grad_cam_loss', grad_cam_loss)

correct_p = tf.equal(tf.argmax(rar_probs, 1), (tf.argmax(Y, 1)))
accuracy = tf.reduce_mean(tf.cast(correct_p, "float"))
tf.summary.scalar('accuracy', accuracy)
train_op = tf.train.AdamOptimizer(lr).minimize(total_loss)
summary_op = tf.summary.merge_all()

def read_and_decode(filename, batch_size):
    filename_queue = tf.train.string_input_producer([filename])
    reader = tf.TFRecordReader()
    _, serialized_example = reader.read(filename_queue)

    feature = {
        'image/encoded': tf.FixedLenFeature((), tf.string, default_value=''),
        'image/format': tf.FixedLenFeature((), tf.string, default_value='png'),
        'image/class/label': tf.FixedLenFeature(
            [], tf.int64, default_value=tf.zeros([], dtype=tf.int64))}

    features = tf.parse_single_example(serialized_example, features=feature)

    Label = tf.one_hot(features['image/class/label'], 10)
    A = tf.image.decode_png(features['image/encoded'])

    image = tf.reshape(A, shape=[32, 32, 3])

    image = tf.image.random_flip_left_right(image)

    preprocessed = tf.multiply(tf.subtract(image / 255, 0.5), 2.0)

    img_batch = tf.train.shuffle_batch([preprocessed, Label], batch_size=batch_size, num_threads=64, capacity=10000,
                                       min_after_dequeue=5000)
    return img_batch


def test_read_and_decode(filename, batch_size):
    filename_queue = tf.train.string_input_producer([filename])
    reader = tf.TFRecordReader()
    _, serialized_example = reader.read(filename_queue)

    feature = {
        'image/encoded': tf.FixedLenFeature((), tf.string, default_value=''),
        'image/format': tf.FixedLenFeature((), tf.string, default_value='png'),
        'image/class/label': tf.FixedLenFeature(
            [], tf.int64, default_value=tf.zeros([], dtype=tf.int64))}

    features = tf.parse_single_example(serialized_example, features=feature)

    Label = tf.one_hot(features['image/class/label'], 10)
    A = tf.image.decode_png(features['image/encoded'])

    A = tf.reshape(A, shape=[32, 32, 3])

    preprocessed = tf.multiply(tf.subtract(A / 255, 0.5), 2.0)

    img_batch = tf.train.batch([preprocessed, Label], batch_size=batch_size, num_threads=64, capacity=1000)
    return img_batch


filename = 'cifar_data/cifar10_train.tfrecord'
test_filename = 'cifar_data/cifar10_test.tfrecord'
train_batch = read_and_decode(filename, _BATCH_SIZE)
test_batch = test_read_and_decode(test_filename, _BATCH_SIZE)
coord = tf.train.Coordinator()

config = tf.ConfigProto()
config.gpu_options.allow_growth = True
sess = tf.Session(config=config)
sess.run(tf.global_variables_initializer())

threads = tf.train.start_queue_runners(coord=coord, sess=sess)

train_writer = tf.summary.FileWriter("model/log/train", sess.graph)
# test_writer = tf.summary.FileWriter("model/log/test", sess.graph)
adv_writer = tf.summary.FileWriter("model/log/adv", sess.graph)



time_start = time.time()
result_file = 'result_3_4_1.txt'
f_w = open(result_file, 'a')
f_w.write('ini')
f_w.close()
for i in range(50000):
    batch = sess.run(train_batch)
    adv_sample = sess.run(fixed_adv_sample_get_op, feed_dict={X: batch[0], keep_prop: 1.0})
    _, acc, summery = sess.run([train_op, accuracy, summary_op],
                               feed_dict={X: batch[0], X_adv: adv_sample, Y: batch[1], keep_prop: 0.5})

    # adv_sample_N1 = sess.run(fixed_adv_sample_get_op, feed_dict={X: adv_sample, keep_prop: 1.0})
    # _, adv_acc, adv_summery = sess.run([train_op, accuracy, summary_op],
    #                                    feed_dict={X: batch[0], X_adv: adv_sample_N1, Y: batch[1], keep_prop: 0.5})
    #
    # adv_sample_N2 = sess.run(fixed_adv_sample_get_op, feed_dict={X: adv_sample_N1, keep_prop: 1.0})
    # _, acc = sess.run([train_op, accuracy], feed_dict={X:  batch[0], X_adv: adv_sample_N2, Y: batch[1], keep_prop: 0.5})

    # adv_sample_N3= sess.run(fixed_adv_sample_get_op, feed_dict={X: adv_sample_N1, keep_prop: 1.0})
    # _, acc = sess.run([train_op, accuracy],
    #                   feed_dict={X:  batch[0], X_adv: adv_sample_N3, Y: batch[1], keep_prop: 0.5})
    print(i)
    if 1 % 100 == 0:
        print(i)
        testbatch = sess.run(test_batch)
        test_acc, test_summ = sess.run([accuracy, summary_op], feed_dict={X: testbatch[0], Y: batch[1], keep_prop: 1.0})
    if 1 % 1000 == 0:
        f_w = open(result_file, 'a')
        f_w.write(str(acc) + " " + str(i) + "\n")
        f_w.close()
    if i % 10000 == 0 and i!=0:
        f_w = open(result_file, 'a')
        f_w.write(str(acc) + " " + str(i) + "\n")
        f_w.close()
        save()
save()


batch = sess.run(train_batch)
R_cam = sess.run(rar_grad_cam, feed_dict={X: batch[0], Y: batch[1], keep_prop: 1.0})
R_mask = sess.run(mask_X, feed_dict={X: batch[0], Y: batch[1], keep_prop: 1.0})
R_mask = np.nan_to_num(R_mask)
ADV_s = sess.run(fixed_adv_sample_get_op, feed_dict={X: batch[0], keep_prop: 1.0})
A_cam = sess.run(rar_grad_cam, feed_dict={X: ADV_s, Y: batch[1], keep_prop: 1.0})
A_mask = sess.run(mask_X, feed_dict={X: ADV_s, Y: batch[1], keep_prop: 1.0})
A_mask = np.nan_to_num(A_mask)
time_end = time.time()
print(str(time_end - time_start) + "s")

print("进行RAR测试集测试:")
ACC = 0
adv_ACC = 0
noise_adv_ACC = 0
double_adv_ACC = 0
for i in range(10000 // _BATCH_SIZE):
    testbatch = sess.run(test_batch)
    adv_sample = sess.run(fixed_adv_sample_get_op, feed_dict={X: testbatch[0], keep_prop: 1.0})
    noise_adv_sample = sess.run(NOISE_adv_sample_get_op, feed_dict={X: testbatch[0], keep_prop: 1.0})
    double_adv_sample = sess.run(fixed_adv_sample_get_op, feed_dict={X: adv_sample, keep_prop: 1.0})

    noise_adv_ACC += sess.run(accuracy, feed_dict={X: noise_adv_sample, Y: testbatch[1], keep_prop: 1.0})
    ACC += sess.run(accuracy, feed_dict={X: testbatch[0], Y: testbatch[1], keep_prop: 1.0})
    adv_ACC += sess.run(accuracy, feed_dict={X: adv_sample, Y: testbatch[1], keep_prop: 1.0})
    double_adv_ACC += sess.run(accuracy, feed_dict={X: double_adv_sample, Y: testbatch[1], keep_prop: 1.0})
print(ACC / (10000 // _BATCH_SIZE))
print(adv_ACC / (10000 // _BATCH_SIZE))
print(noise_adv_ACC / (10000 // _BATCH_SIZE))
print(double_adv_ACC / (10000 // _BATCH_SIZE))

f_w = open(result_file, 'a')
f_w.write("1   ")
f_w.write(str(ACC / (10000 // _BATCH_SIZE)) + " " + str(adv_ACC / (10000 // _BATCH_SIZE)) + "\n")
f_w.write(str(noise_adv_ACC / (10000 // _BATCH_SIZE)) + " " + str(double_adv_ACC / (10000 // _BATCH_SIZE)) + "\n")
f_w.write(str(time_end - time_start) + "s")

f_w.close()

// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.

using System.Reflection;
using System.Collections.Generic;
using AuralPrimer.UI;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit.Interactors;
using UnityEngine.XR.Interaction.Toolkit.Interactors.Visuals;

namespace AuralPrimer.Link.Tests
{
    public class KeyboardProximityTests
    {
        [SetUp]
        public void ResetKeyboard()
        {
            KeyboardProximity.Clear();
            KeyboardProximity.SuppressOverKeys = true;
        }

        [TearDown]
        public void ClearKeyboard() => ResetKeyboard();

        static void PublishBed() => KeyboardProximity.Publish(
            new Vector3(-0.5f, 0f, 0f), new Vector3(0.5f, 0f, 0f), Vector3.forward * 0.14f);

        [Test]
        public void UnknownKeyboardDoesNotKeepAHandSuppressed()
        {
            Assert.That(KeyboardProximity.Distance(Vector3.zero), Is.EqualTo(-1f));
            Assert.IsFalse(KeyboardProximity.ShouldHideRay(-1f, true));
        }

        [TestCase(0f)]
        [TestCase(0.07f)]
        [TestCase(0.14f)]
        public void DistanceIncludesBothEndsOfEachKey(float depth)
        {
            PublishBed();
            Assert.That(KeyboardProximity.Distance(new Vector3(0.1f, 0.30f, depth)), Is.EqualTo(0.30f).Within(1e-5f));
        }

        [Test]
        public void DistanceClampsToTheKeyBedEdges()
        {
            PublishBed();
            Assert.That(KeyboardProximity.Distance(new Vector3(0.8f, 0f, 0.07f)), Is.EqualTo(0.3f).Within(1e-5f));
            Assert.That(KeyboardProximity.Distance(new Vector3(0f, 0f, 0.44f)), Is.EqualTo(0.3f).Within(1e-5f));
            Assert.That(KeyboardProximity.Distance(new Vector3(-0.5f, 0f, 0.07f)), Is.Zero.Within(1e-5f));
        }

        [Test]
        public void RotatedAndTranslatedKeyboardUsesWorldSpace()
        {
            var rotation = Quaternion.Euler(15f, 53f, -8f);
            var origin = new Vector3(2f, 0.8f, -3f);
            KeyboardProximity.Publish(origin + rotation * Vector3.left * 0.5f,
                origin + rotation * Vector3.right * 0.5f, rotation * Vector3.forward * 0.14f);
            var hand = origin + rotation * new Vector3(0.2f, 0.25f, 0.1f);
            Assert.That(KeyboardProximity.Distance(hand), Is.EqualTo(0.25f).Within(1e-5f));
        }

        [Test]
        public void UpdatingCalibrationMovesTheExclusionZone()
        {
            PublishBed();
            Assert.That(KeyboardProximity.Distance(Vector3.zero), Is.Zero);
            KeyboardProximity.Publish(Vector3.up, Vector3.up + Vector3.right);
            Assert.That(KeyboardProximity.Distance(Vector3.zero), Is.EqualTo(1f).Within(1e-5f));
            KeyboardProximity.Clear();
            Assert.That(KeyboardProximity.Distance(Vector3.zero), Is.EqualTo(-1f));
        }

        [Test]
        public void CoincidentCalibrationEdgesStillHaveAFiniteDistance()
        {
            KeyboardProximity.Publish(Vector3.zero, Vector3.zero);
            Assert.That(KeyboardProximity.Distance(Vector3.up), Is.EqualTo(1f));
        }

        [TestCase(0.02f, true)]
        [TestCase(0.30f, true)]
        [TestCase(0.3048f, true)]
        [TestCase(0.305f, false)]
        [TestCase(0.5f, false)]
        public void AHandEnteringWithinOneFootHidesItsRay(float distance, bool hidden)
        {
            Assert.That(KeyboardProximity.ShouldHideRay(distance, false), Is.EqualTo(hidden));
        }

        [Test]
        public void SmallHandMovementsAtTheBoundaryDoNotFlicker()
        {
            var hidden = KeyboardProximity.ShouldHideRay(0.30f, false);
            foreach (var distance in new[] { 0.31f, 0.303f, 0.33f, 0.35f, 0.36f })
            {
                hidden = KeyboardProximity.ShouldHideRay(distance, hidden);
                Assert.IsTrue(hidden);
            }
            Assert.IsFalse(KeyboardProximity.ShouldHideRay(0.361f, hidden));
        }

        [Test]
        public void EachHandKeepsItsOwnBoundaryState()
        {
            Assert.IsTrue(KeyboardProximity.ShouldHideRay(0.32f, true));
            Assert.IsFalse(KeyboardProximity.ShouldHideRay(0.32f, false));
        }

        [Test]
        public void FineTuningCanStillPointAtHandlesOnTheKeys()
        {
            KeyboardProximity.SuppressOverKeys = false;
            Assert.IsFalse(KeyboardProximity.ShouldHideRay(0f, true));
        }

        static GameObject CreateRay(out HandRayDriver driver)
        {
            var ray = new GameObject("Proximity test hand");
            ray.SetActive(false);
            ray.AddComponent<LineRenderer>();
            ray.AddComponent<NearFarInteractor>();
            ray.AddComponent<CurveVisualController>();
            driver = ray.AddComponent<HandRayDriver>();
            Invoke(driver, "Awake");
            return ray;
        }

        static object Invoke(HandRayDriver driver, string method, params object[] arguments) =>
            typeof(HandRayDriver).GetMethod(method, BindingFlags.Instance | BindingFlags.NonPublic)
                .Invoke(driver, arguments);

        static bool WantsRay(HandRayDriver driver, Vector3 knuckle, Vector3 thumb, Vector3 tip) =>
            (bool)Invoke(driver, "WantsRay", knuckle, thumb, tip);

        [Test]
        public void FingertipNearKeysOverridesGazeAtAMenu()
        {
            PublishBed();
            var ray = CreateRay(out var driver);
            var sceneCameras = new List<Camera>();
            foreach (var camera in Object.FindObjectsByType<Camera>(FindObjectsSortMode.None))
                if (camera.CompareTag("MainCamera"))
                {
                    sceneCameras.Add(camera);
                    camera.tag = "Untagged";
                }
            var head = new GameObject("Proximity test head");
            var menu = new GameObject("Proximity test menu");
            try
            {
                head.tag = "MainCamera";
                head.AddComponent<Camera>();
                head.transform.position = new Vector3(0f, 1f, -2f);
                menu.transform.position = new Vector3(0f, 1f, 0f);
                MenuGaze.Register(menu.transform, new Vector2(2f, 2f));
                var raised = new Vector3(0f, 1f, 0.1f);
                Assert.IsTrue(WantsRay(driver, raised, raised, raised));
                Assert.IsFalse(WantsRay(driver, raised, raised, new Vector3(0f, 0.02f, 0.1f)));
                Assert.IsTrue(WantsRay(driver, raised, raised, raised));
            }
            finally
            {
                MenuGaze.Unregister(menu.transform);
                Object.DestroyImmediate(ray);
                Object.DestroyImmediate(head);
                Object.DestroyImmediate(menu);
                foreach (var camera in sceneCameras)
                    if (camera != null) camera.tag = "MainCamera";
            }
        }

        [Test]
        public void HidingTheRayDisablesTheBeforeRenderWriterAndKeepsInteraction()
        {
            var ray = CreateRay(out var driver);
            try
            {
                Invoke(driver, "SetVisible", true);
                Invoke(driver, "SetRayVisible", false);
                Assert.IsFalse(ray.GetComponent<LineRenderer>().enabled);
                Assert.IsFalse(ray.GetComponent<CurveVisualController>().enabled);
                Assert.IsTrue(ray.GetComponent<NearFarInteractor>().enabled);
                Invoke(driver, "SetRayVisible", true);
                Assert.IsTrue(ray.GetComponent<LineRenderer>().enabled);
                Assert.IsTrue(ray.GetComponent<CurveVisualController>().enabled);
            }
            finally { Object.DestroyImmediate(ray); }
        }
    }
}
